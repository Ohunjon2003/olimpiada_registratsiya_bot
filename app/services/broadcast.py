"""Ommaviy xabar yuborish (bot orqali) va rejalashtirilgan xabarlar.

Turlari:
  * message      — oddiy matn (+ rasm) filtrlangan ishtirokchilarga
  * results      — har bir ishtirokchiga o'z natijasi
  * certificates — har bir ishtirokchiga o'z sertifikatlari (rasm + PDF, sovrindorga o'rni bilan tabrik)
  * passes       — ruxsatnoma (olimpiada kuni: sana, vaqt, bino, xona, o'rin)

Telegram cheklovi: ~30 xabar/sekund. Biz sekundiga BROADCAST_RATE (standart 20)
ta yuboramiz. Server qayta ishga tushsa, yuborish to'xtagan joyidan davom etadi.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from aiogram.exceptions import (TelegramBadRequest, TelegramForbiddenError,
                                TelegramRetryAfter)
from aiogram.types import BufferedInputFile, FSInputFile
from sqlalchemy import func, select

from ..config import config
from ..db import session_scope
from ..models import Broadcast, Participant
from ..utils import by_lang, now_utc
from . import certificates as certs
from . import settings as st
from .participants import ParticipantFilter, apply_filter

log = logging.getLogger("broadcast")
_lock = asyncio.Lock()
_wakeup = asyncio.Event()


def recipients_query(b: Broadcast):
    f = ParticipantFilter.from_params(json.loads(b.filters_json or "{}"))
    q = select(Participant).where(Participant.bot_blocked == False)
    if not f.status:
        q = q.where(Participant.status == "active")
    return apply_filter(q, f)


async def count_recipients(b: Broadcast) -> int:
    async with session_scope() as s:
        sub = recipients_query(b).subquery()
        return (await s.execute(select(func.count()).select_from(sub))).scalar_one()


def wake() -> None:
    """Rejalashtiruvchini darhol uyg'otish (yangi xabar qo'shilganda)."""
    _wakeup.set()


async def _send_with_retry(coro_factory) -> str:
    """'ok' | 'blocked' | 'fail'"""
    for _ in range(3):
        try:
            await coro_factory()
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            return "blocked"
        except TelegramBadRequest as e:
            log.info("Yuborib bo'lmadi: %s", e)
            return "fail"
        except Exception as e:  # tarmoq xatosi va h.k.
            log.warning("Yuborishda xato: %s", e)
            await asyncio.sleep(2)
    return "fail"


async def send_certificate(bot, p: Participant, it) -> str:
    """Bitta sertifikat: rasm (telefonda ko'rish uchun) + PDF (chop etish uchun). 'ok' | 'blocked' | 'fail'."""
    from . import design as dz
    img = await asyncio.to_thread(it.render)
    jpg = await asyncio.to_thread(dz.to_jpeg, img)
    pdf = await asyncio.to_thread(dz.to_pdf, img, certs.template_dpi(it.template))
    caption = certs.caption_for(it, p.lang)
    status = await _send_with_retry(lambda: bot.send_photo(
        p.telegram_id, BufferedInputFile(jpg, filename=it.filename.replace(".pdf", ".jpg")), caption=caption))
    if status != "ok":
        return status
    return await _send_with_retry(lambda: bot.send_document(
        p.telegram_id, BufferedInputFile(pdf, filename=it.filename)))


async def send_sheets(bot, session, p: Participant, b: Broadcast) -> str:
    """Tekshirilgan titul(lar) — rasm: to'g'ri yashil, xato qizil."""
    from sqlalchemy import select as _select
    from ..bot.texts import t
    from ..models import SheetScan
    from . import omr_service as osvc
    flt = json.loads(b.filters_json or "{}")
    q = _select(SheetScan).where(SheetScan.participant_id == p.id, SheetScan.status == "applied")
    if b.round_id:
        q = q.where(SheetScan.round_id == b.round_id)
    if str(flt.get("sheet_subject") or "").isdigit():
        q = q.where(SheetScan.subject_id == int(flt["sheet_subject"]))
    scans = (await session.execute(q)).scalars().all()
    if not scans:
        return "skip"
    status = "skip"
    for sc in scans[:10]:
        keys = await osvc.keys_for(session, sc.subject_id, sc.round_id)
        key = keys.get(sc.variant)
        try:
            img = await asyncio.to_thread(osvc.render_scan, sc, key)
        except FileNotFoundError:
            continue
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, "JPEG", quality=88)
        name = sc.subject.name if sc.subject else ""
        if sc.correct is not None:
            caption = t("sheet_caption", p.lang).format(subject=name, correct=sc.correct, total=len(sc.answers),
                                                       percent=f"{sc.percent:g}")
        else:
            caption = t("sheet_caption_nokey", p.lang).format(subject=name)
        data = buf.getvalue()
        status = await _send_with_retry(lambda: bot.send_photo(
            p.telegram_id, BufferedInputFile(data, filename=f"titul_{p.code}_{sc.subject_id}.jpg"), caption=caption))
        if status != "ok":
            return status
        sc.sent_at = now_utc()
    return status


async def run(broadcast_id: int) -> None:
    """Bitta xabarni yuborish (bir vaqtda faqat bittasi ishlaydi)."""
    from ..bot import loader
    from ..bot.formatters import results_text
    from ..bot.handlers.registration import split_text
    from ..bot import stickers
    from ..bot.texts import t

    bot = loader.bot
    if bot is None:
        return
    async with _lock:
        async with session_scope() as s:
            b = await s.get(Broadcast, broadcast_id)
            if b is None or b.status not in ("scheduled", "sending"):
                return
            if b.status == "scheduled":
                b.total = await count_recipients(b)
            b.status = "sending"
            await s.commit()

        photo_id: Optional[str] = None
        delay = 1.0 / max(config.BROADCAST_RATE, 1.0)
        async with session_scope() as s:
            templates = await certs.active_templates(s) if b.kind == "certificates" else []
            if b.kind == "passes" and not st.get("passes_published"):
                await st.set_many(s, {"passes_published": True})  # botda «🎫 Ruxsatnoma» tugmasi chiqadi
        batch = 50
        while True:
            async with session_scope() as s:
                b = await s.get(Broadcast, broadcast_id)
                if b.status != "sending":  # bekor qilingan
                    return
                people = (await s.execute(recipients_query(b).where(Participant.id > b.cursor_id)
                                          .order_by(Participant.id).limit(batch))).scalars().all()
                if not people:
                    b.status = "done"
                    b.finished_at = now_utc()
                    await s.commit()
                    log.info("Xabar #%s yakunlandi: %s yuborildi, %s xato", b.id, b.sent, b.failed)
                    return

                for p in people:
                    status = "fail"
                    if b.kind == "message":
                        text = by_lang(b.text, b.text_cyr, p.lang)
                        if b.photo_path:
                            async def send(p=p, text=text):
                                nonlocal photo_id
                                msg = await bot.send_photo(
                                    p.telegram_id, photo_id or FSInputFile(b.photo_path),
                                    caption=text[:1024] or None, parse_mode=None)
                                if msg.photo and not photo_id:
                                    photo_id = msg.photo[-1].file_id  # keyingilarga qayta yuklamaslik
                            status = await _send_with_retry(send)
                        else:
                            status = await _send_with_retry(
                                lambda p=p, text=text: bot.send_message(
                                    p.telegram_id, text, parse_mode=None, disable_web_page_preview=True))
                    elif b.kind == "results":
                        text = await results_text(s, p)
                        if text:
                            prefix = t("results_ready", p.lang) + "\n\n"
                            if await stickers.send(bot, p.telegram_id, "sticker_results"):
                                await asyncio.sleep(delay)  # Telegram tezlik chegarasi
                            parts = split_text(prefix + text)
                            ok = True
                            for part in parts:
                                r = await _send_with_retry(lambda p=p, part=part: bot.send_message(p.telegram_id, part))
                                if r != "ok":
                                    status, ok = r, False
                                    break
                                await asyncio.sleep(delay)
                            if ok:
                                status = "ok"
                        else:
                            status = "skip"
                    elif b.kind == "certificates":
                        items = await certs.certificates_for(s, p, templates)
                        if not items:
                            status = "skip"
                        for it in items[:10]:
                            status = await send_certificate(bot, p, it)
                            if status != "ok":
                                break
                            await asyncio.sleep(delay * 2)  # har sertifikat — 2 ta xabar
                    elif b.kind == "sheets":
                        status = await send_sheets(bot, s, p, b)
                        await asyncio.sleep(delay * 2)
                    elif b.kind == "passes":
                        from . import seating
                        status = await seating.send_pass(bot, s, p)
                        await asyncio.sleep(delay * 2)

                    if status == "ok":
                        b.sent += 1
                    elif status == "blocked":
                        b.failed += 1
                        p.bot_blocked = True
                    elif status == "fail":
                        b.failed += 1
                    b.cursor_id = p.id
                    # Har ishtirokchidan keyin saqlaymiz: yozish qulfi uzoq ushlanmaydi
                    # (hakamlar ball kiritayotganda kutib qolmasin) va uzilsa joyidan davom etadi
                    await s.commit()
                    await asyncio.sleep(delay)


async def scheduler_loop() -> None:
    """Har 20 soniyada vaqti kelgan xabarlarni tekshiradi."""
    # Server o'chib-yonganida to'xtab qolgan xabarlarni davom ettiramiz
    async with session_scope() as s:
        stuck = (await s.execute(select(Broadcast).where(Broadcast.status == "sending"))).scalars().all()
        ids = [b.id for b in stuck]
    for bid in ids:
        asyncio.create_task(run(bid))
    while True:
        try:
            async with session_scope() as s:
                due = (await s.execute(select(Broadcast.id).where(
                    Broadcast.status == "scheduled", Broadcast.scheduled_at <= now_utc())
                    .order_by(Broadcast.scheduled_at))).scalars().all()
            for bid in due:
                await run(bid)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Rejalashtiruvchida xato")
        _wakeup.clear()
        try:
            await asyncio.wait_for(_wakeup.wait(), timeout=20)
        except asyncio.TimeoutError:
            pass

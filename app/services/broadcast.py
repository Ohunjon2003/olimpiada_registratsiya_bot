"""Ommaviy xabar yuborish (bot orqali) va rejalashtirilgan xabarlar.

Turlari:
  * message      — oddiy matn (+ rasm) filtrlangan ishtirokchilarga
  * results      — har bir ishtirokchiga o'z natijasi
  * certificates — har bir ishtirokchiga o'z sertifikatlari (rasm + PDF, sovrindorga o'rni bilan tabrik)
  * passes       — ruxsatnoma (olimpiada kuni: sana, vaqt, bino, xona, o'rin)
  * sheets       — tekshirilgan titul rasmlari
  * vouchers     — qatnashganlarga voucher

Telegram cheklovi: ~30 xabar/sekund. Biz sekundiga BROADCAST_RATE (standart 20)
ta yuboramiz. Server qayta ishga tushsa, yuborish to'xtagan joyidan davom etadi.
"""
from __future__ import annotations

import asyncio
import json
import logging

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
        key = osvc.key_for(keys, sc.grade_id, sc.variant)
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


class _Rate:
    """Umumiy tezlik chegarasi: sekundiga ko'pi bilan `rate` ta Telegram so'rovi (barcha ishchilar uchun)."""

    def __init__(self, rate: float):
        self.gap = 1.0 / max(rate, 1.0)
        self.next = 0.0
        self.lock = asyncio.Lock()

    async def wait(self, n: int = 1) -> None:
        async with self.lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            start = max(now, self.next)
            self.next = start + self.gap * n
        if start > now:
            await asyncio.sleep(start - now)


async def _deliver(bot, snap, pid: int, templates, rate: _Rate, photo: dict) -> str:
    """Bitta o'quvchiga yuborish — O'Z sessiyasida (bir nechta ishchi parallel ishlaydi).
    Natija: 'ok' | 'blocked' | 'fail' | 'skip'."""
    from ..bot.formatters import results_text
    from ..bot.handlers.registration import split_text
    from ..bot import stickers
    from ..bot.texts import t

    async with session_scope() as s:
        p = await s.get(Participant, pid)
        if p is None:
            return "skip"
        status = "fail"
        if snap.kind == "message":
            text = by_lang(snap.text, snap.text_cyr, p.lang)
            await rate.wait()
            if snap.photo_path:
                async def send(p=p, text=text):
                    msg = await bot.send_photo(p.telegram_id, photo.get("id") or FSInputFile(snap.photo_path),
                                               caption=text[:1024] or None, parse_mode=None)
                    if msg.photo and not photo.get("id"):
                        photo["id"] = msg.photo[-1].file_id      # keyingilarga qayta yuklamaslik
                status = await _send_with_retry(send)
            else:
                status = await _send_with_retry(lambda: bot.send_message(
                    p.telegram_id, text, parse_mode=None, disable_web_page_preview=True))
        elif snap.kind == "results":
            text = await results_text(s, p)
            if text:
                prefix = t("results_ready", p.lang) + "\n\n"
                await rate.wait()
                await stickers.send(bot, p.telegram_id, "sticker_results")
                status = "ok"
                for part in split_text(prefix + text):
                    await rate.wait()
                    r = await _send_with_retry(lambda part=part: bot.send_message(p.telegram_id, part))
                    if r != "ok":
                        status = r
                        break
                if status == "ok" and snap.with_sheets:          # tekshirilgan titul rasmlari ham
                    await rate.wait()
                    r = await send_sheets(bot, s, p, snap)
                    if r in ("blocked",):
                        status = r
            else:
                status = "skip"
        elif snap.kind == "certificates":
            items = await certs.certificates_for(s, p, templates)
            status = "skip" if not items else "ok"
            for it in items[:10]:
                await rate.wait(2)                               # har sertifikat — 2 ta xabar
                status = await send_certificate(bot, p, it)
                if status != "ok":
                    break
        elif snap.kind == "sheets":
            await rate.wait(2)
            status = await send_sheets(bot, s, p, snap)
        elif snap.kind == "passes":
            from . import seating
            await rate.wait(2)
            status = await seating.send_pass(bot, s, p)
        elif snap.kind == "vouchers":
            from . import vouchers
            await rate.wait(2)
            status = await vouchers.send(bot, s, p)
        if status == "blocked":
            p.bot_blocked = True
        await s.commit()          # pass_sent_at / sent_at / bot_blocked — darhol saqlanadi
        return status


async def run(broadcast_id: int) -> None:
    """Bitta xabarni yuborish (bir vaqtda faqat bittasi ishlaydi).

    Tezlik: bir nechta o'quvchiga PARALLEL yuboriladi (BROADCAST_WORKERS, standart 6) — rasm/PDF yuklash
    tarmoqda kutadi, shu vaqtda boshqalariga ham yuboriladi. Umumiy chegara — sekundiga BROADCAST_RATE so'rov.
    Har guruhdan keyin joyi saqlanadi: server qayta yonsa, davom etadi.
    """
    from types import SimpleNamespace
    from ..bot import loader

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
            flt = json.loads(b.filters_json or "{}")
            snap = SimpleNamespace(kind=b.kind, text=b.text, text_cyr=b.text_cyr, photo_path=b.photo_path,
                                   filters_json=b.filters_json, round_id=b.round_id,
                                   with_sheets=flt.get("with_sheets") == "1")

        rate = _Rate(config.BROADCAST_RATE)
        workers = max(1, min(int(getattr(config, "BROADCAST_WORKERS", 6)), 16))
        photo: dict = {}
        async with session_scope() as s:
            templates = await certs.active_templates(s) if snap.kind == "certificates" else []
            if snap.kind == "passes" and not st.get("passes_published"):
                await st.set_many(s, {"passes_published": True})  # botda «🎫 Ruxsatnoma» tugmasi chiqadi
            if snap.kind == "vouchers" and not st.get("vouchers_published"):
                await st.set_many(s, {"vouchers_published": True})  # botda «🎟 Voucher» tugmasi chiqadi
        batch = workers * 2
        sem = asyncio.Semaphore(workers)

        async def one(pid: int) -> str:
            async with sem:
                try:
                    return await _deliver(bot, snap, pid, templates, rate, photo)
                except Exception:
                    log.exception("Xabar #%s: o'quvchi %s ga yuborishda xato", broadcast_id, pid)
                    return "fail"

        while True:
            async with session_scope() as s:
                b = await s.get(Broadcast, broadcast_id)
                if b.status != "sending":  # bekor qilingan
                    return
                ids = (await s.execute(recipients_query(b).with_only_columns(Participant.id)
                                       .where(Participant.id > b.cursor_id)
                                       .order_by(Participant.id).limit(batch))).scalars().all()
                if not ids:
                    b.status = "done"
                    b.finished_at = now_utc()
                    await s.commit()
                    log.info("Xabar #%s yakunlandi: %s yuborildi, %s xato", b.id, b.sent, b.failed)
                    return
            results = await asyncio.gather(*(one(pid) for pid in ids))
            async with session_scope() as s:
                b = await s.get(Broadcast, broadcast_id)
                b.sent += sum(1 for r in results if r == "ok")
                b.failed += sum(1 for r in results if r in ("blocked", "fail"))
                b.cursor_id = max(ids)
                await s.commit()


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

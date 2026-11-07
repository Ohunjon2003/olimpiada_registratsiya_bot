"""Voucherlar: olimpiadada qatnashgan o'quvchilarga pul qiymatli voucher.

Jarayon:
  1. Admin «🎟 Voucherlar»da qiymat (masalan 2 000 000 so'm), matn va kimga berilishini tanlaydi
     (kelganlar — davomat bo'yicha / natijasi borlar / hammaga).
  2. «Voucher berish» — har o'quvchiga bitta noyob raqam (V-0127-K3F9QZ). Qayta bosilsa,
     faqat yangi qo'shilganlarga beriladi — eskilari o'zgarmaydi.
  3. Botga yuborish (rasm + PDF) va/yoki maktablar bo'yicha chop etish (PDF).
  4. Voucherdagi QR → https://<sayt>/v/<raqam>: istalgan telefon kamerasi bilan ochiladi va
     «✅ Haqiqiy / ishlatilgan / bekor qilingan» ko'rinadi. Admin shu sahifada «Ishlatildi» deb belgilaydi —
     ikkinchi marta ishlatib bo'lmaydi.
"""
from __future__ import annotations

import asyncio
import secrets
from datetime import date, datetime
from typing import Optional

from sqlalchemy import func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import Attendance, CertificateTemplate, Participant, Result, Round, Voucher
from ..utils import by_lang, fmt_dt, grade_num, lat2cyr, now_utc
from . import ranking
from . import settings as st

WHO = {"attended": "Olimpiadaga kelganlar (davomatda «Keldi»)",
       "results": "Natijasi kiritilganlar",
       "all": "Ro'yxatdagi barcha o'quvchilar"}
STATUS = {"active": "✅ Amalda", "redeemed": "☑️ Ishlatilgan", "cancelled": "⛔ Bekor qilingan"}
_ALPHA = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # o'xshash belgilar yo'q (0/O, 1/I)

# ---------------------------------------------------------------------------
# Summa: raqam va so'z bilan
# ---------------------------------------------------------------------------
_ONES = ["", "bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti", "sakkiz", "to'qqiz"]
_TENS = ["", "o'n", "yigirma", "o'ttiz", "qirq", "ellik", "oltmish", "yetmish", "sakson", "to'qson"]


def _under_1000(n: int) -> list[str]:
    out = []
    if n >= 100:
        out += ([_ONES[n // 100]] if n // 100 > 1 else ["bir"]) + ["yuz"]
        n %= 100
    if n >= 10:
        out.append(_TENS[n // 10])
        n %= 10
    if n:
        out.append(_ONES[n])
    return out


def amount_words(n: int) -> str:
    """2000000 → «ikki million», 1500000 → «bir million besh yuz ming»."""
    if n <= 0:
        return "nol"
    words = []
    for value, name in ((10 ** 9, "milliard"), (10 ** 6, "million"), (1000, "ming")):
        if n >= value:
            words += _under_1000(n // value) + [name]
            n %= value
    words += _under_1000(n)
    return " ".join(words)


def fmt_amount(n: int) -> str:
    """2000000 → «2 000 000»"""
    return f"{int(n):,}".replace(",", " ")


# ---------------------------------------------------------------------------
# Raqam, havola
# ---------------------------------------------------------------------------
def new_serial(code: str) -> str:
    prefix = (st.get("voucher_prefix") or "V").strip().upper()[:6] or "V"
    rnd = "".join(secrets.choice(_ALPHA) for _ in range(6))
    return f"{prefix}-{code or '0'}-{rnd}"


def site_url() -> str:
    return (st.get("site_url") or "").strip().rstrip("/")


def verify_url(serial: str) -> str:
    """QR ichidagi havola. Sayt manzili kiritilmagan bo'lsa — faqat raqam (tekshirishda qo'lda kiritiladi)."""
    base = site_url()
    return f"{base}/v/{serial}" if base else serial


def valid_until() -> Optional[date]:
    raw = (st.get("voucher_valid_until") or "").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
    except ValueError:
        return None


def expired(today: Optional[date] = None) -> bool:
    vu = valid_until()
    return bool(vu and (today or datetime.now(config.TZ).date()) > vu)


# ---------------------------------------------------------------------------
# Kimga beriladi
# ---------------------------------------------------------------------------
def eligible_query(round_: Round, who: str):
    q = select(Participant).where(ranking.eligible_condition(round_), Participant.status == "active")
    if who == "attended":
        q = q.where(Participant.id.in_(select(Attendance.participant_id).where(
            Attendance.round_id == round_.id, Attendance.present == True)))  # noqa: E712
    elif who == "results":
        q = q.where(Participant.id.in_(select(Result.participant_id).where(Result.round_id == round_.id)))
    return q


async def stats(session: AsyncSession, round_: Round) -> dict:
    who = st.get("voucher_who") or "attended"
    eligible = (await session.execute(select(func.count()).select_from(
        eligible_query(round_, who).subquery()))).scalar_one()
    rows = (await session.execute(select(Voucher).where(Voucher.round_id == round_.id))).scalars().all()
    active = [v for v in rows if v.status == "active"]
    redeemed = [v for v in rows if v.status == "redeemed"]
    have = {v.participant_id for v in rows}
    pending = (await session.execute(select(func.count()).select_from(
        eligible_query(round_, who).where(Participant.id.not_in(have) if have else true()).subquery()))).scalar_one()
    return {
        "who": who, "eligible": eligible, "issued": len(rows), "pending": pending,
        "active": len(active), "redeemed": len(redeemed), "cancelled": len(rows) - len(active) - len(redeemed),
        "sent": sum(1 for v in rows if v.sent_at and not v.cancelled_at),
        "unsent": sum(1 for v in active if not v.sent_at),
        "sum_issued": sum(v.amount for v in rows if v.status != "cancelled"),
        "sum_redeemed": sum(v.amount for v in redeemed),
    }


async def issue(session: AsyncSession, round_: Round, by: str) -> int:
    """Hali voucher olmagan mos o'quvchilarga voucher yaratadi. Qaytaradi: nechta yaratildi."""
    who = st.get("voucher_who") or "attended"
    amount = int(st.get("voucher_amount") or 0)
    if amount <= 0:
        raise ValueError("Voucher qiymatini kiriting")
    have = set((await session.execute(select(Voucher.participant_id).where(Voucher.round_id == round_.id))).scalars())
    people = (await session.execute(eligible_query(round_, who).order_by(Participant.id))).scalars().all()
    used = set((await session.execute(select(Voucher.serial))).scalars())
    made = 0
    for p in people:
        if p.id in have:
            continue
        serial = new_serial(p.code)
        while serial in used:
            serial = new_serial(p.code)
        used.add(serial)
        session.add(Voucher(participant_id=p.id, round_id=round_.id, serial=serial, amount=amount, issued_by=by[:64]))
        made += 1
    await session.flush()
    return made


async def find(session: AsyncSession, serial: str) -> Optional[Voucher]:
    serial = (serial or "").strip().upper()
    if "/V/" in serial:                     # butun havola kiritilgan bo'lsa
        serial = serial.rsplit("/V/", 1)[1]
    serial = serial.strip("/ ")
    if not serial or len(serial) > 32:
        return None
    return (await session.execute(select(Voucher).where(func.upper(Voucher.serial) == serial))).scalars().first()


async def for_participant(session: AsyncSession, p: Participant) -> Optional[Voucher]:
    """O'quvchining joriy turdagi (yoki eng so'nggi) amaldagi voucheri."""
    return (await session.execute(select(Voucher).where(Voucher.participant_id == p.id, Voucher.cancelled_at.is_(None))
                                  .order_by(Voucher.id.desc()))).scalars().first()


# ---------------------------------------------------------------------------
# Dizayn va yuborish
# ---------------------------------------------------------------------------
def build_context(v: Voucher) -> dict:
    p = v.participant
    lang = p.lang
    conv = (lambda s: lat2cyr(s)) if lang == "cyr" else (lambda s: s)
    vu = valid_until()
    ctx = {
        "full_name": p.full_name, "first_name": p.first_name, "last_name": p.last_name,
        "school": by_lang(p.school.name, p.school.name_cyr, lang) if p.school else "",
        "grade": grade_num(by_lang(p.grade.name, p.grade.name_cyr, lang)) if p.grade else "",
        "code": p.code or "", "olympiad": st.get("olympiad_name"),
        "round": by_lang(v.round.name, v.round.name_cyr, lang) if v.round else "",
        "date": fmt_dt(v.issued_at, "%d.%m.%Y"),
        "amount": fmt_amount(v.amount), "amount_words": conv(amount_words(v.amount)),
        "currency": conv("so'm"),
        "reason": conv(st.get("voucher_reason") or ""),
        "terms": conv(st.get("voucher_terms") or ""),
        "voucher_no": v.serial,
        "valid_until": vu.strftime("%d.%m.%Y") if vu else conv("muddatsiz"),
        "qr": verify_url(v.serial),
        "lang": lang,
    }
    return ctx


async def template(session: AsyncSession) -> Optional[CertificateTemplate]:
    return (await session.execute(select(CertificateTemplate).where(
        CertificateTemplate.kind == "voucher", CertificateTemplate.is_active == True)  # noqa: E712
        .order_by(CertificateTemplate.position, CertificateTemplate.id))).scalars().first()


def render(tpl: CertificateTemplate, v: Voucher):
    from . import certificates as certs
    return certs.render_template(tpl, build_context(v))


def caption(v: Voucher) -> str:
    from html import escape
    from ..bot.texts import t
    p = v.participant
    ctx = build_context(v)
    text = t("voucher_caption", p.lang).format(
        name=escape(p.full_name), amount=ctx["amount"], reason=escape(ctx["reason"]), serial=escape(v.serial),
        valid=escape(ctx["valid_until"]))
    return text[:1024]


async def send(bot, session: AsyncSession, p: Participant) -> str:
    """O'quvchiga voucheri (rasm + PDF). 'ok' | 'blocked' | 'fail' | 'skip'."""
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
    from aiogram.types import BufferedInputFile
    from . import certificates as certs
    from . import design as dz
    v = await for_participant(session, p)
    tpl = await template(session)
    if v is None or tpl is None or p.status != "active" or v.status != "active":
        return "skip"
    img = await asyncio.to_thread(render, tpl, v)
    jpg = await asyncio.to_thread(dz.to_jpeg, img)
    pdf = await asyncio.to_thread(dz.to_pdf, img, certs.template_dpi(tpl))
    cap = caption(v)
    photo_done = False    # qayta urinishda rasm ikki marta bormasin
    for _ in range(3):
        try:
            if not photo_done:
                await bot.send_photo(p.telegram_id, BufferedInputFile(jpg, f"voucher_{p.code}.jpg"), caption=cap)
                photo_done = True
            await bot.send_document(p.telegram_id, BufferedInputFile(pdf, f"voucher_{p.code}.pdf"))
            v.sent_at = now_utc()
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
        except TelegramForbiddenError:
            p.bot_blocked = True
            return "blocked"
        except TelegramBadRequest:
            return "fail"
        except Exception:
            await asyncio.sleep(2)
    return "fail"

"""Avtomatik tekshiruv — baza bilan ishlash: skanerni qabul qilish, natija yozish, tekshirish navbati,
kalit o'zgarganda qayta hisoblash, titullarni chop etish ro'yxati.

Qoidalar:
  * Natija o'quvchiga faqat titul QR'idagi ID va o'quvchi o'zi belgilagan ID mos kelsa yoziladi.
    Mos kelmasa yoki ID belgilanmagan bo'lsa — «Tekshirish kerak» (admin hal qiladi).
  * Shu titul qayta skaner qilinsa: javoblar bir xil — «oldin kiritilgan» ogohlantirishi, hech narsa o'zgarmaydi;
    farq qilsa — eski natija saqlanadi, yangisi admin tasdig'iga tushadi.
  * Kalit kiritilmagan bo'lsa ham skaner qilish mumkin — ball kalit kiritilgach hisoblanadi.
  * Hakam natijani qo'lda o'zgartirsa, kalit o'zgarganda qayta hisoblash uni bosib ketmaydi.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import (AnswerKey, Participant, Result, Round, SeatAssignment,
                      SheetScan, Subject, User)
from ..utils import now_utc
from . import audit, omr, ranking
from . import settings as st
from .scoring import ScoreError, compute

STATUS = {"applied": "Qabul qilingan", "review": "Tekshirish kerak", "rejected": "Rad etilgan",
          "superseded": "Almashtirilgan"}
REASONS = {"id_mismatch": "QR va o'quvchi belgilagan ID farq qiladi", "id_empty": "O'quvchi ID belgilamagan",
           "differs": "Oldin kiritilgan natijadan farq qiladi", "manual_exists": "Qo'lda kiritilgan natija bor",
           "unclear": "Ba'zi javoblar aniq o'qilmadi", "not_subject": "O'quvchi bu fanni tanlamagan"}


def scan_dir() -> Path:
    d = config.UPLOAD_DIR / "scans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def scan_path(name: str) -> Optional[Path]:
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    p = scan_dir() / name
    return p if p.exists() else None


def code_digits(p: Participant, d: int) -> str:
    return (p.code or str(p.id)).zfill(d)[-d:]


# ---------------------------------------------------------------------------
# Kalitlar
# ---------------------------------------------------------------------------
async def keys_for(session: AsyncSession, subject_id: int, round_id: int) -> dict[int, str]:
    rows = (await session.execute(select(AnswerKey).where(AnswerKey.subject_id == subject_id,
                                                          AnswerKey.round_id == round_id))).scalars().all()
    return {k.variant: k.answers for k in rows if k.answers}


def omr_ready(subject: Subject) -> Optional[str]:
    """Fan avtomatik tekshiruvga yaroqlimi: None — ha, aks holda sabab."""
    if subject.scoring_mode == "manual":
        return "«Qo'lda» baholanadigan fanda titul bo'lmaydi (savollar soni yo'q)"
    n = subject.question_total
    if not 1 <= n <= omr.MAX_QUESTIONS:
        return f"Savollar soni 1..{omr.MAX_QUESTIONS} bo'lishi kerak (hozir {n})"
    return None


def variant_for(p: Participant, seat: Optional[SeatAssignment], variants: int) -> int:
    if variants <= 1:
        return 0
    if seat is not None and seat.room is not None:
        return omr.variant_for_seat(seat.row, seat.desk, seat.seat, seat.room.seats_per_desk, variants)
    return p.id % variants


# ---------------------------------------------------------------------------
# Natijani yozish
# ---------------------------------------------------------------------------
def compute_scan(subject: Subject, answers: str, key: str):
    ok = omr.grade(answers, key)
    try:
        return compute(subject, omr.score_values(subject, ok)), sum(ok)
    except ScoreError:
        return None, sum(ok)


async def write_result(session: AsyncSession, scan: SheetScan, subject: Subject, key: Optional[str],
                       user_id: Optional[int]) -> bool:
    """Skaner natijasini Result jadvaliga yozadi (kalit bo'lsa). True — yozildi."""
    if not key or len(key) != len(scan.answers):
        scan.correct = scan.score = scan.percent = None
        return False
    c, correct = compute_scan(subject, scan.answers, key)
    if c is None:
        return False
    scan.correct, scan.score, scan.percent = correct, c.score, c.percent
    details = dict(c.details)
    details["scan"] = scan.id
    now = now_utc()
    vals = dict(details=json.dumps(details), correct_total=c.correct_total, score=c.score, max_score=c.max_score,
                percent=c.percent, entered_by_id=user_id, updated_at=now)
    stmt = sqlite_insert(Result).values(participant_id=scan.participant_id, subject_id=scan.subject_id,
                                        round_id=scan.round_id, entered_at=now, **vals)
    stmt = stmt.on_conflict_do_update(index_elements=["participant_id", "subject_id", "round_id"], set_=vals)
    await session.execute(stmt)
    ranking.invalidate()
    return True


async def existing_result(session: AsyncSession, pid: int, sid: int, rid: int) -> Optional[Result]:
    return (await session.execute(select(Result).where(Result.participant_id == pid, Result.subject_id == sid,
                                                       Result.round_id == rid))).scalars().first()


def result_scan_id(res: Optional[Result]) -> Optional[int]:
    if res is None:
        return None
    try:
        return json.loads(res.details or "{}").get("scan")
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Skanerni qabul qilish
# ---------------------------------------------------------------------------
@dataclass
class ScanReply:
    state: str          # nosheet | partial | error | unclear | ok | dup | review
    message: str
    token: str = ""
    scan_id: Optional[int] = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {"state": self.state, "message": self.message, "token": self.token, "scan_id": self.scan_id,
                "detail": self.detail}


def _save_images(data: bytes, warped, stamp: str) -> tuple[str, str]:
    import cv2
    raw = Image.open(BytesIO(data))
    raw = raw.convert("RGB")
    raw.thumbnail((1600, 1600))
    raw_name = f"{stamp}_raw.jpg"
    raw.save(scan_dir() / raw_name, "JPEG", quality=80)
    w_name = f"{stamp}_sheet.jpg"
    cv2.imwrite(str(scan_dir() / w_name), warped, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return w_name, raw_name


async def process(session: AsyncSession, user: User, data: bytes, force: bool = False) -> ScanReply:
    """Bitta kadr/surat: o'qish → tekshirish → saqlash. Sahifa uchun javob qaytaradi."""
    r = await asyncio.to_thread(omr.read_image, data)
    if r.state != "ok":
        return ScanReply(r.state, r.message, r.token.raw if r.token else "")
    tok = r.token
    subject = await session.get(Subject, tok.sid)
    round_ = await session.get(Round, tok.rid)
    p = await session.get(Participant, tok.pid)
    if subject is None or round_ is None or p is None:
        return ScanReply("error", "Titul egasi, fani yoki turi bazada topilmadi", tok.raw)
    head = f"ID {p.code} · {subject.name}"
    if not user.is_admin and subject.id not in {s.id for s in user.subjects}:
        return ScanReply("error", f"{head}: bu fan sizga biriktirilmagan", tok.raw)
    if not subject.omr_enabled:
        return ScanReply("error", f"{head}: bu fan uchun avtomatik tekshiruv o'chirilgan", tok.raw)
    if subject.is_locked or round_.is_locked:
        return ScanReply("error", f"{head}: natijalar qulflangan", tok.raw)
    if tok.n != subject.question_total:
        return ScanReply("error", f"{head}: titulda {tok.n} ta savol, fanda esa {subject.question_total} ta. "
                                  "Titullarni qayta chop eting.", tok.raw)
    if r.unclear and not force:
        qs = ", ".join(map(str, r.unclear[:8])) + ("…" if len(r.unclear) > 8 else "")
        return ScanReply("unclear", f"{head}: aniq o'qilmadi — {qs}-savol(lar). Yorug'roq joyda qayta skaner qiling "
                                    "yoki «Admin tekshirsin» tugmasini bosing.", tok.raw)

    expected = code_digits(p, tok.d)
    marked = r.id_marked
    reason = ""
    if marked == expected:
        pass
    elif set(marked) == {"_"}:
        if st.get("omr_id_required"):
            reason = "id_empty"
    else:
        reason = "id_mismatch"
    if not reason and r.unclear:
        reason = "unclear"
    if not reason and subject.id not in {s.id for s in p.subjects}:
        reason = "not_subject"

    # Takroriy skaner
    applied = (await session.execute(select(SheetScan).where(
        SheetScan.participant_id == p.id, SheetScan.subject_id == subject.id, SheetScan.round_id == round_.id,
        SheetScan.status == "applied"))).scalars().first()
    if applied and applied.answers == r.answers and not reason:
        res = f"{applied.correct}/{tok.n}" if applied.correct is not None else "kalit kutilmoqda"
        return ScanReply("dup", f"⚠️ {head}: bu titul oldin kiritilgan ({res}). Natija o'zgarmadi.", tok.raw, applied.id)
    pending = (await session.execute(select(SheetScan).where(
        SheetScan.qr_participant_id == p.id, SheetScan.subject_id == subject.id, SheetScan.round_id == round_.id,
        SheetScan.status == "review", SheetScan.answers == r.answers, SheetScan.id_marked == marked))).scalars().first()
    if pending:
        return ScanReply("review", f"⚠️ {head}: bu titul allaqachon admin tekshiruvida ({REASONS.get(pending.reason, '')}).",
                         tok.raw, pending.id)
    if not reason and applied:
        reason = "differs"
    if not reason:
        res = await existing_result(session, p.id, subject.id, round_.id)
        if res is not None and result_scan_id(res) is None:
            reason = "manual_exists"

    stamp = f"{datetime.now(config.TZ):%Y%m%d_%H%M%S}_{p.id}_{subject.id}_{now_utc().microsecond}"
    w_name, raw_name = await asyncio.to_thread(_save_images, data, r.warped, stamp)
    scan = SheetScan(participant_id=p.id, qr_participant_id=p.id, subject_id=subject.id, round_id=round_.id,
                     variant=tok.variant, answers=r.answers, id_marked=marked, image=w_name, raw_image=raw_name,
                     scanned_by=user.username, status="review" if reason else "applied", reason=reason)
    session.add(scan)
    await session.flush()
    keys = await keys_for(session, subject.id, round_.id)
    key = keys.get(tok.variant)
    if reason:
        # Taxminiy ball (ko'rsatish uchun), natijaga yozilmaydi
        if key and len(key) == len(scan.answers):
            c, correct = compute_scan(subject, scan.answers, key)
            if c:
                scan.correct, scan.score, scan.percent = correct, c.score, c.percent
        audit.log(session, user, "create", "scan", scan.id, f"{head}: tekshirishga — {REASONS[reason]}")
        await session.commit()
        msg = {"id_mismatch": f"🛑 {head}: o'quvchi titulga boshqa ID belgilagan ({marked.replace('_', '·')}). "
                              "Titul almashib qolgan bo'lishi mumkin — natija yozilmadi, admin tekshiradi.",
               "id_empty": f"⚠️ {head}: o'quvchi ID raqamini belgilamagan — natija admin tasdiqlagach yoziladi.",
               "differs": f"⚠️ {head}: oldin kiritilgan natijadan farq qiladi — eski natija saqlandi, yangisi admin tasdig'ida.",
               "manual_exists": f"⚠️ {head}: bu o'quvchiga natija qo'lda kiritilgan — admin tasdiqlaydi.",
               "unclear": f"⚠️ {head}: aniq o'qilmagan javoblar bilan admin tekshiruviga yuborildi.",
               "not_subject": f"⚠️ {head}: o'quvchi bu fanni tanlamagan — admin tekshiradi."}[reason]
        return ScanReply("review", msg, tok.raw, scan.id)
    wrote = await write_result(session, scan, subject, key, user.id)
    audit.log(session, user, "create", "scan", scan.id,
              f"{head}: skaner — " + (f"{scan.correct}/{tok.n}" if wrote else "kalit yo'q"))
    await session.commit()
    if wrote:
        return ScanReply("ok", f"✅ Tasdiqlandi: {head} — {scan.correct}/{tok.n} to'g'ri ({scan.percent:g}%)", tok.raw, scan.id)
    return ScanReply("ok", f"✅ Qabul qilindi: {head}. Ball kalit kiritilgach avtomatik hisoblanadi.", tok.raw, scan.id)


# ---------------------------------------------------------------------------
# Admin qarorlari va qayta hisoblash
# ---------------------------------------------------------------------------
async def resolve(session: AsyncSession, scan: SheetScan, action: str, user: User,
                  answers: Optional[str] = None, target_code: str = "") -> str:
    """action: accept — QR'dagi o'quvchiga; accept_marked — boshqa ID'dagi o'quvchiga; reject."""
    subject = await session.get(Subject, scan.subject_id)
    round_ = await session.get(Round, scan.round_id)
    if subject.is_locked or round_.is_locked:
        raise ValueError("Natijalar qulflangan")
    if answers is not None:
        a = "".join(ch for ch in answers.upper() if not ch.isspace())
        if len(a) != len(scan.answers) or any(ch not in omr.OPTIONS + "-*" for ch in a):
            raise ValueError(f"Javoblar {len(scan.answers)} ta bo'lishi va faqat A, B, C, D, «-» (bo'sh), "
                             "«*» (bir nechta) belgilaridan iborat bo'lishi kerak")
        scan.answers = a
    if action == "reject":
        scan.status, scan.decided_by, scan.decided_at = "rejected", user.username, now_utc()
        audit.log(session, user, "update", "scan", scan.id, "Skaner rad etildi")
        await session.commit()
        return "Rad etildi"
    if "?" in scan.answers:
        raise ValueError("Aniq o'qilmagan javoblar («?») bor — ularni A/B/C/D, «-» yoki «*» bilan almashtiring")
    if action == "accept_marked":
        code = (target_code or scan.id_marked or "").strip()
        if not code.isdigit():
            raise ValueError("O'quvchi ID raqamini kiriting")
        target = (await session.execute(select(Participant).where(Participant.code == code.zfill(4)))).scalars().first()
        if target is None:
            raise ValueError(f"ID {code} li o'quvchi topilmadi")
        scan.participant_id = target.id
    # Shu o'quvchining oldingi qabul qilingan skanerini almashtiramiz
    for old in (await session.execute(select(SheetScan).where(
            SheetScan.participant_id == scan.participant_id, SheetScan.subject_id == scan.subject_id,
            SheetScan.round_id == scan.round_id, SheetScan.status == "applied", SheetScan.id != scan.id))).scalars():
        old.status = "superseded"
    scan.status, scan.decided_by, scan.decided_at = "applied", user.username, now_utc()
    await session.flush()
    keys = await keys_for(session, scan.subject_id, scan.round_id)
    wrote = await write_result(session, scan, subject, keys.get(scan.variant), user.id)
    p = await session.get(Participant, scan.participant_id)
    audit.log(session, user, "update", "scan", scan.id, f"Skaner tasdiqlandi → ID {p.code}"
              + (f": {scan.correct}/{len(scan.answers)}" if wrote else " (kalit yo'q)"))
    await session.commit()
    return f"Tasdiqlandi: ID {p.code}" + (f" — {scan.correct}/{len(scan.answers)} ({scan.percent:g}%)" if wrote else "")


async def regrade(session: AsyncSession, subject: Subject, round_id: int, user: Optional[User]) -> tuple[int, int]:
    """Kalit o'zgargach barcha qabul qilingan skanerlarni qayta hisoblash. (yangilandi, o'tkazib yuborildi)"""
    keys = await keys_for(session, subject.id, round_id)
    done = skipped = 0
    scans = (await session.execute(select(SheetScan).where(SheetScan.subject_id == subject.id,
                                                           SheetScan.round_id == round_id,
                                                           SheetScan.status.in_(("applied", "review"))))).scalars().all()
    for sc in scans:
        key = keys.get(sc.variant)
        if sc.status == "review":   # taxminiy ballni yangilash
            if key and len(key) == len(sc.answers):
                c, correct = compute_scan(subject, sc.answers, key)
                if c:
                    sc.correct, sc.score, sc.percent = correct, c.score, c.percent
            continue
        res = await existing_result(session, sc.participant_id, subject.id, round_id)
        if res is not None and result_scan_id(res) not in (None, sc.id):
            skipped += 1
            continue
        if res is not None and result_scan_id(res) is None:
            skipped += 1   # hakam/admin qo'lda o'zgartirgan — tegmaymiz
            continue
        if await write_result(session, sc, subject, key, user.id if user else None):
            done += 1
    await session.flush()
    return done, skipped


# ---------------------------------------------------------------------------
# Titullar ro'yxati (chop etish)
# ---------------------------------------------------------------------------
async def cards(session: AsyncSession, subject: Subject, round_: Round, exam_session_id: Optional[int] = None):
    """[(CardInfo, Layout)] — joylashtirish bo'lsa smena/xona/o'rin tartibida, bo'lmasa maktab va familiya bo'yicha."""
    from . import seating
    n = subject.question_total
    people = (await session.execute(select(Participant).where(ranking.eligible_condition(round_))
                                    .order_by(Participant.id))).scalars().all()
    people = [p for p in people if subject.id in {s.id for s in p.subjects}]
    seats = {a.participant_id: a for a in (await session.execute(
        select(SeatAssignment).where(SeatAssignment.round_id == round_.id))).scalars().all()}
    if exam_session_id:
        people = [p for p in people if seats.get(p.id) and seats[p.id].session_id == exam_session_id]

    def order(p):
        """Joylanganlar — smena, xona, o'rin tartibida (xonaga tayyor dasta); qolganlar — maktab, familiya."""
        a = seats.get(p.id)
        if a:
            return (0, a.session.day.isoformat(), a.session.start_time, a.session_id, a.room.position, a.room_id,
                    a.row, a.desk, a.seat, "")
        return (1, "", "", 0, 0, 0, 0, 0, 0, (p.school.name if p.school else "") + p.last_name + p.first_name)
    people.sort(key=order)
    show_name = bool(st.get("omr_show_name"))
    olymp = st.get("olympiad_name")
    out = []
    for p in people:
        d = max(4, len(p.code or ""))
        a = seats.get(p.id)
        var = variant_for(p, a, subject.variants)
        place = ""
        if a:
            place = f"{seating.room_label(a.room)} · {seating.seat_number(a.room, (a.row, a.desk, a.seat))}-o'rin"
        info = omr.CardInfo(token=omr.make_token(p.id, subject.id, round_.id, var, n, d), olympiad=olymp,
                            subject=subject.name, code=code_digits(p, d), place=place,
                            variant=f"{omr.variant_letter(var)} variant" if subject.variants > 1 else "",
                            name=p.full_name if show_name else "",
                            session=f"{a.session.name}" if a else "")
        out.append((info, omr.layout(n, d)))
    return out


def render_scan(scan: SheetScan, key: Optional[str], title: bool = True) -> Image.Image:
    """Tekshirilgan nusxa (to'g'ri — yashil, xato — qizil)."""
    import cv2
    p = scan_path(scan.image)
    if p is None:
        raise FileNotFoundError("Titul rasmi topilmadi")
    warped = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
    n = len(scan.answers)
    d = max(4, len(scan.id_marked))
    head = ""
    if title:
        name = scan.subject.name if scan.subject else ""
        code = scan.participant.code if scan.participant else ""
        res = f"{scan.correct}/{n} ({scan.percent:g}%)" if scan.correct is not None else "kalit kutilmoqda"
        head = f"ID {code} · {name} · {res}"
    return omr.annotate(warped, n, d, scan.answers, key or "", scan.id_marked, head)

"""Ishtirokchilar bilan ishlash: qidiruv matni, filtrlar, kod berish."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import Attendance, Participant, participant_subjects
from ..utils import search_norm


def build_search_text(p: Participant) -> str:
    """Ism, familiya, telefon, kod va manzilni bitta qidiruv satriga yig'adi."""
    digits = "".join(c for c in (p.phone or "") if c.isdigit())
    parts = [p.code or "", p.first_name, p.last_name, p.last_name + " " + p.first_name,
             digits, digits[-9:], p.tg_username or "", p.instagram_username or ""]
    return search_norm(" | ".join(parts))


def assign_code(p: Participant) -> None:
    """Ishtirokchi raqami: id asosida, 4 xonali (0001). id flush'dan keyin ma'lum bo'ladi."""
    p.code = str(p.id).zfill(4)


@dataclass
class ParticipantFilter:
    q: str = ""
    school_id: Optional[int] = None
    grade_id: Optional[int] = None
    subject_id: Optional[int] = None
    status: str = ""
    lang: str = ""
    date_from: str = ""  # YYYY-MM-DD (mahalliy sana)
    date_to: str = ""
    attendance: str = ""  # came | absent | unmarked — davomat bo'yicha
    att_round: Optional[int] = None  # davomat qaysi turga tegishli (bo'sh — joriy tur)

    @classmethod
    def from_params(cls, params) -> "ParticipantFilter":
        def as_int(name):
            v = (params.get(name) or "").strip()
            return int(v) if v.isdigit() else None
        return cls(
            q=(params.get("q") or "").strip(),
            school_id=as_int("school_id"), grade_id=as_int("grade_id"),
            subject_id=as_int("subject_id"),
            status=(params.get("status") or "").strip(),
            lang=(params.get("lang") or "").strip(),
            date_from=(params.get("date_from") or "").strip(),
            date_to=(params.get("date_to") or "").strip(),
            attendance=(params.get("attendance") or "").strip(),
            att_round=as_int("att_round"),
        )

    def as_dict(self) -> dict:
        return {k: ("" if v is None else v) for k, v in self.__dict__.items()}


def _local_day_to_utc(day: str) -> Optional[datetime]:
    try:
        d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=config.TZ)
    except ValueError:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def apply_filter(q, f: ParticipantFilter):
    """SQL so'roviga filtrlarni qo'shadi."""
    if f.q:
        needle = search_norm(f.q)
        if needle:
            q = q.where(Participant.search_text.contains(needle, autoescape=True))
    if f.school_id:
        q = q.where(Participant.school_id == f.school_id)
    if f.grade_id:
        q = q.where(Participant.grade_id == f.grade_id)
    if f.subject_id:
        sub = select(participant_subjects.c.participant_id).where(
            participant_subjects.c.subject_id == f.subject_id)
        q = q.where(Participant.id.in_(sub))
    if f.attendance in ("came", "absent", "unmarked") and f.att_round:
        marked = select(Attendance.participant_id).where(Attendance.round_id == f.att_round)
        if f.attendance == "came":
            q = q.where(Participant.id.in_(marked.where(Attendance.present == True)))  # noqa: E712
        elif f.attendance == "absent":
            q = q.where(Participant.id.in_(marked.where(Attendance.present == False)))  # noqa: E712
        else:
            q = q.where(Participant.id.not_in(marked))
    if f.status:
        q = q.where(Participant.status == f.status)
    if f.lang:
        q = q.where(Participant.lang == f.lang)
    if f.date_from:
        start = _local_day_to_utc(f.date_from)
        if start:
            q = q.where(Participant.created_at >= start)
    if f.date_to:
        end = _local_day_to_utc(f.date_to)
        if end:
            q = q.where(Participant.created_at < end + timedelta(days=1))
    return q


async def phone_taken(session: AsyncSession, phone: str, exclude_telegram_id: int | None = None) -> bool:
    q = select(func.count()).select_from(Participant).where(Participant.phone == phone)
    if exclude_telegram_id is not None:
        q = q.where(Participant.telegram_id != exclude_telegram_id)
    return (await session.execute(q)).scalar_one() > 0


async def by_telegram(session: AsyncSession, telegram_id: int) -> Optional[Participant]:
    return (await session.execute(select(Participant).where(
        Participant.telegram_id == telegram_id))).scalars().first()


async def resolve_att_round(session: AsyncSession, f: ParticipantFilter) -> None:
    """Davomat filtri berilgan, lekin tur ko'rsatilmagan bo'lsa — joriy turni qo'yadi."""
    if f.attendance and not f.att_round:
        from .ranking import current_round
        cur = await current_round(session)
        f.att_round = cur.id if cur else None

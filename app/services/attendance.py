"""Davomat: o'quvchi olimpiadaga keldi / kelmadi."""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Attendance, Participant, Round
from ..utils import now_utc
from .ranking import eligible_condition

LABELS = {True: "✅ Keldi", False: "❌ Kelmadi", None: "❔ Belgilanmagan"}


async def status_map(session: AsyncSession, round_id: int,
                     pids: Optional[Iterable[int]] = None) -> dict[int, bool]:
    """participant_id → True (keldi) / False (kelmadi). Belgilanmaganlar ro'yxatda bo'lmaydi."""
    q = select(Attendance.participant_id, Attendance.present).where(Attendance.round_id == round_id)
    if pids is not None:
        pids = list(pids)
        if not pids:
            return {}
        q = q.where(Attendance.participant_id.in_(pids))
    return dict((await session.execute(q)).all())


async def counts(session: AsyncSession, round_: Round) -> dict:
    """Shu turda qatnashishi kerak bo'lganlar bo'yicha: jami, keldi, kelmadi, belgilanmagan."""
    total = (await session.execute(select(func.count()).select_from(Participant)
                                   .where(eligible_condition(round_)))).scalar_one()
    rows = dict((await session.execute(
        select(Attendance.present, func.count()).join(Participant, Participant.id == Attendance.participant_id)
        .where(Attendance.round_id == round_.id, eligible_condition(round_))
        .group_by(Attendance.present))).all())
    came, absent = rows.get(True, 0), rows.get(False, 0)
    return {"total": total, "came": came, "absent": absent, "unmarked": max(total - came - absent, 0)}


async def mark(session: AsyncSession, participant_id: int, round_id: int,
               present: Optional[bool], by: str) -> None:
    """Belgilash. present=None — belgini olib tashlash. Bir vaqtda bir necha kishi belgilasa ham xavfsiz."""
    if present is None:
        await session.execute(delete(Attendance).where(Attendance.participant_id == participant_id,
                                                       Attendance.round_id == round_id))
    else:
        vals = {"present": present, "marked_by": by[:64], "marked_at": now_utc()}
        stmt = sqlite_insert(Attendance).values(participant_id=participant_id, round_id=round_id, **vals)
        stmt = stmt.on_conflict_do_update(index_elements=["participant_id", "round_id"], set_=vals)
        await session.execute(stmt)
    await session.commit()

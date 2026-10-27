"""Reyting hisoblash: fan bo'yicha va blok (bir nechta fan) bo'yicha.

O'rin berish usuli — 'competition ranking': teng ball olganlar bir xil o'rinni
oladi, keyingi o'rin sakraydi (1, 2, 2, 4).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (Block, Participant, Result, Round, Subject,
                      participant_subjects, round_participants)


@dataclass
class RankRow:
    participant: Participant
    score: float
    max_score: float
    percent: float
    value: float                      # reyting qaysi qiymat bo'yicha tuzilgan
    place: int = 0
    group_size: int = 0               # shu guruhda (sinf/umumiy) nechta ishtirokchi
    result: Optional[Result] = None   # fan reytingi uchun
    parts: dict[int, Optional[Result]] = field(default_factory=dict)  # blok uchun: fan_id → natija


# --- Oddiy TTL kesh: bot ko'p so'rov yuborganda bazani qayta hisoblamaslik uchun ---
_cache: dict[tuple, tuple[float, list[RankRow]]] = {}
_TTL = 30.0


def invalidate() -> None:
    """Natija o'zgarganda keshni tozalash."""
    _cache.clear()


def eligible_condition(round_: Round):
    """Shu turda qatnasha oladigan ishtirokchilar sharti."""
    cond = Participant.status == "active"
    if not round_.open_to_all:
        sub = select(round_participants.c.participant_id).where(
            round_participants.c.round_id == round_.id)
        cond = and_(cond, Participant.id.in_(sub))
    return cond


def _assign_places(rows: list[RankRow], by_grade: bool) -> list[RankRow]:
    """Guruhlarga ajratib (sinf yoki umumiy) o'rin beradi."""
    groups: dict[object, list[RankRow]] = {}
    for r in rows:
        key = r.participant.grade_id if by_grade else 0
        groups.setdefault(key, []).append(r)

    out: list[RankRow] = []
    # Sinflarni tartib raqami bo'yicha chiqaramiz
    def grade_order(key):
        if not by_grade:
            return (0, "")
        g = groups[key][0].participant.grade
        return (g.position if g else 0, g.name if g else "")

    for key in sorted(groups, key=grade_order):
        items = groups[key]
        # Asosiy: qiymat ↓; ikkinchi darajali (faqat ko'rinish tartibi): foiz ↓, familiya
        items.sort(key=lambda r: (-round(r.value, 4), -r.percent, r.participant.last_name,
                                  r.participant.first_name))
        prev_val, place = None, 0
        for i, r in enumerate(items):
            v = round(r.value, 4)
            if v != prev_val:
                place = i + 1
                prev_val = v
            r.place = place
            r.group_size = len(items)
        out.extend(items)
    return out


async def subject_ranking(session: AsyncSession, subject: Subject, round_: Round, *,
                          by_grade: bool = False, grade_id: int | None = None,
                          school_id: int | None = None, use_cache: bool = True) -> list[RankRow]:
    key = ("s", subject.id, round_.id, by_grade, grade_id, school_id)
    if use_cache and key in _cache and time.monotonic() - _cache[key][0] < _TTL:
        return _cache[key][1]

    q = (select(Result).join(Participant, Participant.id == Result.participant_id)
         .where(Result.subject_id == subject.id, Result.round_id == round_.id,
                eligible_condition(round_)))
    if grade_id:
        q = q.where(Participant.grade_id == grade_id)
    if school_id:
        q = q.where(Participant.school_id == school_id)
    results = (await session.execute(q)).scalars().all()

    rows = [RankRow(participant=r.participant, score=r.score, max_score=r.max_score,
                    percent=r.percent, value=r.score, result=r) for r in results]
    rows = _assign_places(rows, by_grade)
    _cache[key] = (time.monotonic(), rows)
    return rows


async def block_ranking(session: AsyncSession, block: Block, round_: Round, *,
                        by_grade: bool = False, grade_id: int | None = None,
                        school_id: int | None = None, use_cache: bool = True) -> list[RankRow]:
    key = ("b", block.id, round_.id, by_grade, grade_id, school_id)
    if use_cache and key in _cache and time.monotonic() - _cache[key][0] < _TTL:
        return _cache[key][1]

    subjects = {s.id: s for s in block.subjects}
    if not subjects:
        return []

    # Ishtirokchi qaysi blok fanlarini tanlagani
    ps_rows = (await session.execute(
        select(participant_subjects.c.participant_id, participant_subjects.c.subject_id)
        .where(participant_subjects.c.subject_id.in_(subjects.keys())))).all()
    chosen: dict[int, set[int]] = {}
    for pid, sid in ps_rows:
        chosen.setdefault(pid, set()).add(sid)
    if block.require_all:
        chosen = {pid: s for pid, s in chosen.items() if s == set(subjects)}
    if not chosen:
        return []

    q = (select(Result).join(Participant, Participant.id == Result.participant_id)
         .where(Result.subject_id.in_(subjects.keys()), Result.round_id == round_.id,
                Result.participant_id.in_(chosen.keys()), eligible_condition(round_)))
    if grade_id:
        q = q.where(Participant.grade_id == grade_id)
    if school_id:
        q = q.where(Participant.school_id == school_id)
    results = (await session.execute(q)).scalars().all()

    by_participant: dict[int, dict[int, Result]] = {}
    people: dict[int, Participant] = {}
    for r in results:
        by_participant.setdefault(r.participant_id, {})[r.subject_id] = r
        people[r.participant_id] = r.participant

    rows: list[RankRow] = []
    for pid, res in by_participant.items():
        # Blokdagi BARCHA fanlar hisobga olinadi: tanlanmagan yoki natijasi yo'q fan = 0 ball.
        # Shunda bir fanda qatnashgan o'quvchi uch fanda qatnashganlardan oldinga o'tib ketmaydi.
        sids = list(subjects)
        score = sum(res[s].score for s in sids if s in res)
        max_score = sum(subjects[s].max_score for s in sids)
        percent = round(score / max_score * 100, 2) if max_score else 0.0
        if block.aggregation == "avg_percent":
            # Yo'q natija 0% hisoblanadi
            value = round(sum(res[s].percent if s in res else 0 for s in sids) / len(sids), 2)
        else:
            value = round(score, 2)
        rows.append(RankRow(participant=people[pid], score=round(score, 2), max_score=max_score,
                            percent=percent if block.aggregation == "sum" else value,
                            value=value, parts={s: res.get(s) for s in chosen[pid]}))
    rows = _assign_places(rows, by_grade)
    _cache[key] = (time.monotonic(), rows)
    return rows


async def participant_summary(session: AsyncSession, participant: Participant, round_: Round,
                              by_grade: bool) -> dict:
    """Bot uchun: ishtirokchining shu turdagi fan va blok natijalari hamda o'rinlari."""
    subjects_out = []
    for subj in participant.subjects:
        rows = await subject_ranking(session, subj, round_, by_grade=by_grade)
        row = next((r for r in rows if r.participant.id == participant.id), None)
        subjects_out.append({"subject": subj, "row": row})

    blocks_out = []
    blocks = (await session.execute(select(Block).where(Block.is_active == True)
                                    .order_by(Block.position, Block.id))).scalars().all()
    my = {s.id for s in participant.subjects}
    for b in blocks:
        if not my & {s.id for s in b.subjects}:
            continue
        rows = await block_ranking(session, b, round_, by_grade=by_grade)
        row = next((r for r in rows if r.participant.id == participant.id), None)
        if row:
            blocks_out.append({"block": b, "row": row})
    return {"subjects": subjects_out, "blocks": blocks_out}


async def current_round(session: AsyncSession) -> Optional[Round]:
    """Joriy tur (is_current). Bo'lmasa birinchi faol tur."""
    r = (await session.execute(select(Round).where(Round.is_current == True))).scalars().first()
    if r:
        return r
    return (await session.execute(select(Round).where(Round.is_active == True)
                                  .order_by(Round.position, Round.id))).scalars().first()


def participants_in_round_query(round_: Round, subject_id: int):
    """Hakam uchun: shu turda shu fanni tanlagan ishtirokchilar."""
    sub = select(participant_subjects.c.participant_id).where(
        participant_subjects.c.subject_id == subject_id)
    return select(Participant).where(Participant.id.in_(sub), eligible_condition(round_))

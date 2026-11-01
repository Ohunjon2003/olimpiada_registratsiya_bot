"""Davomat: olimpiada kuni kirishda «Keldi / Kelmadi» belgilash.

Jonli qidiruv — yozish bilan natija chiqadi (Enter shart emas). Bir nechta odam
bir vaqtda belgilashi mumkin.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Grade, Participant, Round, School, SeatAssignment, Subject, User
from ...services import attendance as att_svc
from ...services import ranking, seating
from ...services import settings as st
from ...services.participants import ParticipantFilter, apply_filter
from ...utils import search_norm
from ..core import current_user, int_or_none, render, verify_csrf

router = APIRouter(prefix="/attendance", dependencies=[Depends(verify_csrf)])

LIMIT = 150  # bir sahifada ko'pi bilan nechta qator


async def attendance_user(user: User = Depends(current_user)) -> User:
    """Admin — har doim; hakam — admin ruxsat bergan bo'lsa (ism-familiyani ko'radi)."""
    if not user.is_admin and not st.get("judges_attendance"):
        raise HTTPException(403, "Davomatni belgilashga ruxsat yo'q. Admin «Umumiy sozlamalar»da ruxsat berishi mumkin.")
    return user


async def _round(session: AsyncSession, round_id: Optional[int]) -> Round:
    if round_id:
        r = await session.get(Round, round_id)
        if r:
            return r
    cur = await ranking.current_round(session)
    if cur is None:
        raise HTTPException(400, "Tur yaratilmagan. «Turlar» bo'limida tur qo'shing.")
    return cur


async def _rows(session: AsyncSession, params: dict, round_: Round):
    """Filtr va qidiruv bo'yicha o'quvchilar, davomat holati bilan."""
    f = ParticipantFilter.from_params(params)
    f.q = ""  # qidiruvni o'zimiz (tartiblash bilan) qilamiz
    f.att_round = round_.id
    q = apply_filter(select(Participant).where(ranking.eligible_condition(round_)), f)
    needle = search_norm(params.get("q", ""))
    order = [Participant.last_name, Participant.first_name]
    if needle:
        q = q.where(Participant.search_text.contains(needle, autoescape=True))
        # Familiyasi/ismi yoki ID'si yozilgan harflar bilan BOSHLANADIGANLAR birinchi chiqadi
        like = needle.replace("%", "").replace("_", "") + "%"
        order.insert(0, case((func.lower(Participant.last_name).like(like), 0),
                             (func.lower(Participant.first_name).like(like), 1),
                             (Participant.code.like(like), 0), else_=2))
    if f.room_id and not needle:  # xona tanlangan — o'rin tartibida (nazoratchi uchun qulay)
        seat_order = (select(SeatAssignment.row * 1000 + SeatAssignment.desk * 10 + SeatAssignment.seat)
                      .where(SeatAssignment.participant_id == Participant.id, SeatAssignment.round_id == round_.id)
                      .scalar_subquery())
        order = [seat_order] + order
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(*order).limit(LIMIT))).scalars().all()
    status = await att_svc.status_map(session, round_.id, [p.id for p in rows])
    return rows, status, total


async def _seats(session: AsyncSession, round_id: int, pids: list[int]) -> dict[int, str]:
    """pid → «204 · 14-o'rin» (o'rin biriktirilgan bo'lsa)."""
    if not pids:
        return {}
    rows = (await session.execute(select(SeatAssignment).where(SeatAssignment.round_id == round_id,
                                                               SeatAssignment.participant_id.in_(pids)))).scalars().all()
    return {a.participant_id: f"{a.room.name} · {seating.seat_number(a.room, (a.row, a.desk, a.seat))}"
            for a in rows if a.room}


@router.get("")
async def attendance_page(request: Request, user: User = Depends(attendance_user),
                          session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    round_ = await _round(session, int_or_none(params.get("round_id")))
    rows, status, total = await _rows(session, params, round_)
    schools = (await session.execute(select(School).where(School.is_active == True)  # noqa: E712
                                     .order_by(School.position, School.name))).scalars().all()
    grades = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    exam_sessions = await seating.round_sessions(session, round_.id)
    rooms = await seating.active_rooms(session) if exam_sessions else []
    return render(request, "attendance/page.html", {
        "round": round_, "rounds": rounds, "rows": rows, "status": status, "total": total,
        "limit": LIMIT, "params": params, "schools": schools, "grades": grades, "subjects": subjects,
        "counts": await att_svc.counts(session, round_), "exam_sessions": exam_sessions, "rooms": rooms,
        "seats": await _seats(session, round_.id, [p.id for p in rows]),
    })


@router.get("/rows")
async def attendance_rows(request: Request, user: User = Depends(attendance_user),
                          session: AsyncSession = Depends(get_session)):
    """HTMX: faqat jadval qatorlari (qidiruv/filtr o'zgarganda)."""
    params = dict(request.query_params)
    round_ = await _round(session, int_or_none(params.get("round_id")))
    rows, status, total = await _rows(session, params, round_)
    return render(request, "attendance/_rows.html", {
        "round": round_, "rows": rows, "status": status, "total": total, "limit": LIMIT,
        "counts": await att_svc.counts(session, round_), "oob": True,
        "seats": await _seats(session, round_.id, [p.id for p in rows]),
    })


@router.post("/mark")
async def attendance_mark(request: Request, user: User = Depends(attendance_user),
                          session: AsyncSession = Depends(get_session)):
    """HTMX: bitta o'quvchini belgilash. value: 1 — keldi, 0 — kelmadi, bo'sh — belgini olish."""
    form = await request.form()
    pid = int_or_none(form.get("participant_id"))
    round_ = await _round(session, int_or_none(form.get("round_id")))
    p = await session.get(Participant, pid or 0)
    if p is None:
        raise HTTPException(404, "O'quvchi topilmadi")
    value = str(form.get("value", ""))
    present = {"1": True, "0": False}.get(value)
    await att_svc.mark(session, p.id, round_.id, present, user.username)
    return render(request, "attendance/_row.html", {
        "p": p, "st": present, "round": round_, "counts": await att_svc.counts(session, round_), "oob": True,
        "seat": (await _seats(session, round_.id, [p.id])).get(p.id, ""),
    })

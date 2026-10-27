"""Hakamlar: ball kiritish sahifasi.

Bir vaqtda ko'p hakam kirita oladi: har bir qator alohida saqlanadi (HTMX),
sahifa yangilanmaydi. Ikki hakam bir qatorni bir vaqtda saqlasa ham
ma'lumot buzilmaydi (UNIQUE cheklov + qayta urinish).
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Attendance, Grade, Participant, Result, Round, School, Subject, User
from ...services import audit, ranking
from ...services import settings as st
from ...services.scoring import ScoreError, compute
from ...utils import now_utc, search_norm
from ..core import (Page, client_ip, current_user, int_or_none, render,
                    verify_csrf)

router = APIRouter(prefix="/judge", dependencies=[Depends(verify_csrf)])


async def allowed_subjects(session: AsyncSession, user: User) -> list[Subject]:
    """Admin va fan biriktirilmagan hakam — barcha faol fanlar."""
    if user.is_admin or not user.subjects:
        return list((await session.execute(select(Subject).where(Subject.is_active == True)
                                           .order_by(Subject.position, Subject.id))).scalars().all())
    return sorted([s for s in user.subjects if s.is_active], key=lambda s: (s.position, s.id))


def show_names(user: User) -> bool:
    return user.is_admin or bool(st.get("judges_see_names"))


@router.get("")
async def judge_home(request: Request, user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    subjects = await allowed_subjects(session, user)
    cur = await ranking.current_round(session)
    items = []
    if cur:
        for s in subjects:
            total = (await session.execute(select(func.count()).select_from(
                ranking.participants_in_round_query(cur, s.id).subquery()))).scalar_one()
            entered = (await session.execute(select(func.count()).select_from(Result).where(
                Result.subject_id == s.id, Result.round_id == cur.id))).scalar_one()
            mine = (await session.execute(select(func.count()).select_from(Result).where(
                Result.subject_id == s.id, Result.round_id == cur.id, Result.entered_by_id == user.id))).scalar_one()
            items.append({"subject": s, "total": total, "entered": entered, "mine": mine})
    return render(request, "judge/home.html", {"items": items, "round": cur})


async def _round_for(session, user: User, round_id: Optional[int]) -> Round:
    cur = await ranking.current_round(session)
    if user.is_admin and round_id:
        r = await session.get(Round, round_id)
        if r:
            return r
    if cur is None:
        raise HTTPException(400, "Tur yaratilmagan. Admin «Turlar» bo'limida tur qo'shishi kerak.")
    return cur


@router.get("/enter")
async def enter_page(request: Request, user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    subjects = await allowed_subjects(session, user)
    if not subjects:
        return render(request, "judge/enter.html", {"subjects": [], "rows": []})
    sid = int_or_none(params.get("subject_id"))
    subject = next((s for s in subjects if s.id == sid), subjects[0])
    round_ = await _round_for(session, user, int_or_none(params.get("round_id")))
    names = show_names(user)

    q = ranking.participants_in_round_query(round_, subject.id)
    grade_id, school_id = int_or_none(params.get("grade_id")), int_or_none(params.get("school_id"))
    if grade_id:
        q = q.where(Participant.grade_id == grade_id)
    if school_id:
        q = q.where(Participant.school_id == school_id)
    search = (params.get("q") or "").strip()
    if search:
        if names:
            q = q.where(Participant.search_text.contains(search_norm(search), autoescape=True))
        else:
            # Ismlar yashirilgan — faqat ID bo'yicha qidiruv
            q = q.where(Participant.code.startswith(search.zfill(4) if search.isdigit() else search,
                                                    autoescape=True))
    done_sub = select(Result.participant_id).where(Result.subject_id == subject.id, Result.round_id == round_.id)
    status = params.get("status", "")
    if status == "missing":
        q = q.where(Participant.id.not_in(done_sub))
    elif status == "done":
        q = q.where(Participant.id.in_(done_sub))
    # Davomat: faqat kelganlar (yoki kelmaganlar) — hakam kelmaganlarni ko'rib o'tirmasin
    att = params.get("attendance", "")
    if att in ("came", "absent"):
        marked = select(Attendance.participant_id).where(Attendance.round_id == round_.id,
                                                         Attendance.present == (att == "came"))
        q = q.where(Participant.id.in_(marked))

    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    per_page = int_or_none(params.get("per_page")) or 50
    per_page = per_page if per_page in (25, 50, 100, 200) else 50
    page = Page(total, int_or_none(params.get("page")) or 1, per_page, params)
    order = [Participant.id] if not names else [Participant.grade_id, Participant.last_name, Participant.first_name]
    people = (await session.execute(q.order_by(*order).offset(page.offset).limit(page.per_page))).scalars().all()
    results = {}
    if people:
        rs = (await session.execute(select(Result).where(
            Result.subject_id == subject.id, Result.round_id == round_.id,
            Result.participant_id.in_([p.id for p in people])))).scalars().all()
        results = {r.participant_id: r for r in rs}

    entered = (await session.execute(select(func.count()).select_from(done_sub.subquery()))).scalar_one()
    all_n = (await session.execute(select(func.count()).select_from(
        ranking.participants_in_round_query(round_, subject.id).subquery()))).scalar_one()
    grades = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
    schools = (await session.execute(select(School).order_by(School.position, School.name))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all() if user.is_admin else []
    rows = [_row_ctx(p, subject, round_, results.get(p.id), names) for p in people]
    return render(request, "judge/enter.html", {
        "subjects": subjects, "subject": subject, "round": round_, "rounds": rounds, "rows": rows,
        "page": page, "params": params, "grades": grades, "schools": schools, "names": names,
        "entered": entered, "all_n": all_n, "locked": subject.is_locked or round_.is_locked})


def _row_ctx(p: Participant, subject: Subject, round_: Round, result: Optional[Result], names: bool,
             state: str = "", error: str = "", values: Optional[dict] = None) -> dict:
    if values is None:
        values = json.loads(result.details or "{}") if result else {}
    return {"p": p, "subject": subject, "round": round_, "result": result, "names": names,
            "state": state, "error": error, "values": values,
            "locked": subject.is_locked or round_.is_locked}


async def _check_access(session, user: User, form) -> tuple[Participant, Subject, Round]:
    pid, sid, rid = (int_or_none(form.get(k)) for k in ("participant_id", "subject_id", "round_id"))
    subjects = await allowed_subjects(session, user)
    subject = next((s for s in subjects if s.id == sid), None)
    if subject is None:
        raise HTTPException(403, "Bu fanga ball kiritishga ruxsatingiz yo'q")
    round_ = await session.get(Round, rid or 0)
    cur = await ranking.current_round(session)
    if round_ is None or (not user.is_admin and (cur is None or round_.id != cur.id)):
        raise HTTPException(400, "Tur noto'g'ri yoki joriy emas. Sahifani yangilang.")
    p = (await session.execute(ranking.participants_in_round_query(round_, subject.id)
                               .where(Participant.id == (pid or 0)))).scalars().first()
    if p is None:
        raise HTTPException(404, "Ishtirokchi bu fan/turda topilmadi (fanni tanlamagan yoki chetlatilgan)")
    return p, subject, round_


@router.post("/result")
async def save_result(request: Request, user: User = Depends(current_user),
                      session: AsyncSession = Depends(get_session)):
    form = await request.form()
    p, subject, round_ = await _check_access(session, user, form)
    names = show_names(user)
    values = {k: str(v) for k, v in form.items() if k == "correct" or k == "score" or
              (k.startswith("g") and k[1:].isdigit())}
    existing = (await session.execute(select(Result).where(
        Result.participant_id == p.id, Result.subject_id == subject.id,
        Result.round_id == round_.id))).scalars().first()

    if subject.is_locked or round_.is_locked:
        return render(request, "judge/_row.html", _row_ctx(p, subject, round_, existing, names, "error",
                                                         "Natijalar qulflangan — o'zgartirib bo'lmaydi", values))
    try:
        c = compute(subject, values)
    except ScoreError as e:
        return render(request, "judge/_row.html", _row_ctx(p, subject, round_, existing, names, "error",
                                                         str(e), values))

    # Atomar UPSERT: ikki hakam bir vaqtda saqlasa ham xato bo'lmaydi va dublikat paydo bo'lmaydi
    old = None if existing is None else {"details": existing.details, "score": existing.score,
                                         "percent": existing.percent, "by": existing.entered_by_id}
    now = now_utc()
    vals = dict(details=json.dumps(c.details), correct_total=c.correct_total, score=c.score,
                max_score=c.max_score, percent=c.percent, entered_by_id=user.id, updated_at=now)
    stmt = sqlite_insert(Result).values(participant_id=p.id, subject_id=subject.id, round_id=round_.id,
                                        entered_at=now, **vals)
    stmt = stmt.on_conflict_do_update(index_elements=["participant_id", "subject_id", "round_id"], set_=vals)
    await session.execute(stmt)
    audit.log(session, user, "update" if old else "create", "result", f"{p.code}/{subject.id}/{round_.id}",
              f"{p.code} · {subject.name} · {round_.name}: {c.score:g} ball",
              old=old, new={"details": c.details, "score": c.score, "percent": c.percent},
              ip=client_ip(request))
    await session.commit()
    existing = (await session.execute(select(Result).where(
        Result.participant_id == p.id, Result.subject_id == subject.id, Result.round_id == round_.id)
        .execution_options(populate_existing=True))).scalars().first()
    ranking.invalidate()
    return render(request, "judge/_row.html", _row_ctx(p, subject, round_, existing, names, "saved"))


@router.post("/result/clear")
async def clear_result(request: Request, user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    form = await request.form()
    p, subject, round_ = await _check_access(session, user, form)
    names = show_names(user)
    existing = (await session.execute(select(Result).where(
        Result.participant_id == p.id, Result.subject_id == subject.id,
        Result.round_id == round_.id))).scalars().first()
    if subject.is_locked or round_.is_locked:
        return render(request, "judge/_row.html", _row_ctx(p, subject, round_, existing, names, "error",
                                                         "Natijalar qulflangan"))
    if existing:
        audit.log(session, user, "delete", "result", f"{p.code}/{subject.id}/{round_.id}",
                  f"{p.code} · {subject.name}: natija o'chirildi",
                  old={"details": existing.details, "score": existing.score}, ip=client_ip(request))
        await session.delete(existing)
        await session.commit()
        ranking.invalidate()
    return render(request, "judge/_row.html", _row_ctx(p, subject, round_, None, names, ""))


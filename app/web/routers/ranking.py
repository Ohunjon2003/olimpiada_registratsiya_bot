"""Admin: reyting (fan va blok bo'yicha), Excel eksport, keyingi turga o'tkazish."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import config
from ...db import get_session
from ...models import (Block, Grade, Round, School, Subject, User,
                       round_participants)
from ...services import audit, export, ranking
from ...services import settings as st
from ..core import client_ip, flash, int_or_none, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/ranking", dependencies=[Depends(verify_csrf)])


async def _resolve(session: AsyncSession, params: dict):
    """So'rov parametrlaridan reyting obyektini (fan yoki blok) va turni aniqlaydi."""
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    blocks = (await session.execute(select(Block).order_by(Block.position, Block.id))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    target = params.get("target") or (f"s:{subjects[0].id}" if subjects else "")
    kind, _, tid = target.partition(":")
    obj = None
    if kind == "s":
        obj = next((s for s in subjects if str(s.id) == tid), None)
    elif kind == "b":
        obj = next((b for b in blocks if str(b.id) == tid), None)
    round_ = next((r for r in rounds if r.id == int_or_none(params.get("round_id"))), None) \
        or await ranking.current_round(session)
    by_grade = params.get("by_grade", "1" if st.get("ranking_by_grade") else "0") == "1"
    return subjects, blocks, rounds, kind, obj, round_, by_grade, target


async def _rows(session, kind, obj, round_, by_grade, params):
    kw = dict(by_grade=by_grade, grade_id=int_or_none(params.get("grade_id")),
              school_id=int_or_none(params.get("school_id")), use_cache=False)
    if obj is None or round_ is None:
        return []
    if kind == "s":
        return await ranking.subject_ranking(session, obj, round_, **kw)
    return await ranking.block_ranking(session, obj, round_, **kw)


@router.get("")
async def ranking_page(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    subjects, blocks, rounds, kind, obj, round_, by_grade, target = await _resolve(session, params)
    rows = await _rows(session, kind, obj, round_, by_grade, params)
    missing = 0
    if obj is not None and round_ is not None and kind == "s":
        total = (await session.execute(select(func.count()).select_from(
            ranking.participants_in_round_query(round_, obj.id).subquery()))).scalar_one()
        missing = max(0, total - len(rows)) if not (params.get("grade_id") or params.get("school_id")) else None
    grades = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
    schools = (await session.execute(select(School).order_by(School.position, School.name))).scalars().all()
    details = {}
    if kind == "s" and obj is not None:
        for r in rows:
            details[r.participant.id] = json.loads(r.result.details or "{}") if r.result else {}
    qs = urlencode({k: v for k, v in {**params, "target": target, "by_grade": "1" if by_grade else "0"}.items() if v})
    return render(request, "admin/ranking.html", {
        "subjects": subjects, "blocks": blocks, "rounds": rounds, "kind": kind, "obj": obj, "round": round_,
        "by_grade": by_grade, "rows": rows, "params": params, "target": target, "grades": grades,
        "schools": schools, "details": details, "missing": missing, "qs": qs})


@router.get("/export")
async def ranking_export(request: Request, user: User = Depends(require_admin),
                         session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    subjects, blocks, rounds, kind, obj, round_, by_grade, target = await _resolve(session, params)
    if obj is None or round_ is None:
        raise HTTPException(404, "Fan/blok yoki tur topilmadi")
    rows = await _rows(session, kind, obj, round_, by_grade, params)
    title = f"{obj.name} - {round_.name}"
    if kind == "s":
        data = await asyncio.to_thread(export.subject_ranking_xlsx, title, obj, rows)
    else:
        data = await asyncio.to_thread(export.block_ranking_xlsx, title, obj.subjects, rows, obj.aggregation)
    safe = "".join(c if c.isalnum() else "_" for c in obj.name)[:40]
    name = f"reyting_{safe}_{datetime.now(config.TZ):%Y%m%d_%H%M}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/advance")
async def advance(request: Request, user: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    """Reytingdagi eng yaxshilarni keyingi turga o'tkazish."""
    form = dict(await request.form())
    params = {k: str(v) for k, v in form.items() if k in ("target", "round_id", "by_grade", "grade_id", "school_id")}
    back = "/admin/ranking?" + urlencode({k: v for k, v in params.items() if v})
    subjects, blocks, rounds, kind, obj, round_, by_grade, target = await _resolve(session, params)
    dest = await session.get(Round, int_or_none(form.get("dest_round_id")) or 0)
    if dest is None or round_ is None or obj is None:
        flash(request, "Tur yoki fan tanlanmagan", "error")
        return RedirectResponse(back, 303)
    if dest.id == round_.id:
        flash(request, "Ishtirokchilarni boshqa turga o'tkazing", "error")
        return RedirectResponse(back, 303)
    rows = await _rows(session, kind, obj, round_, by_grade, params)
    mode = form.get("mode")
    try:
        value = float(str(form.get("value", "")).replace(",", "."))
    except ValueError:
        flash(request, "Qiymat son bo'lishi kerak", "error")
        return RedirectResponse(back, 303)
    if mode == "top":
        chosen = [r for r in rows if r.place <= value]
    elif mode == "percent":
        chosen = [r for r in rows if r.percent >= value]
    else:
        chosen = [r for r in rows if r.score >= value]
    existing = set((await session.execute(select(round_participants.c.participant_id)
                                          .where(round_participants.c.round_id == dest.id))).scalars().all())
    new_ids = [r.participant.id for r in chosen if r.participant.id not in existing]
    if new_ids:
        await session.execute(insert(round_participants), [{"round_id": dest.id, "participant_id": pid} for pid in new_ids])
    if dest.open_to_all:
        dest.open_to_all = False  # endi faqat o'tkazilganlar qatnashadi
    audit.log(session, user, "advance", "rounds", dest.id,
              f"{obj.name}: {len(new_ids)} ta ishtirokchi «{dest.name}»ga o'tkazildi ({mode} {value:g})",
              ip=client_ip(request))
    await session.commit()
    ranking.invalidate()
    flash(request, f"{len(new_ids)} ta ishtirokchi «{dest.name}»ga o'tkazildi"
                   + (f" ({len(chosen) - len(new_ids)} tasi oldin o'tkazilgan edi)" if len(chosen) > len(new_ids) else ""))
    return RedirectResponse(back, 303)

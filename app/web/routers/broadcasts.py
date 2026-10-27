"""Admin: ommaviy xabarlar (darhol yoki rejalashtirilgan), natija va sertifikat yuborish."""
from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ...config import config
from ...db import get_session
from ...models import Broadcast, Grade, Round, School, Subject, User
from ...services import audit, ranking
from ...services import broadcast as bsvc
from ...services import settings as st
from ...utils import now_utc, parse_local_input
from ..core import (Page, client_ip, flash, int_or_none, render,
                    require_admin, verify_csrf)

router = APIRouter(prefix="/admin/broadcasts", dependencies=[Depends(verify_csrf)])

FILTER_KEYS = ("school_id", "grade_id", "subject_id", "lang", "status", "attendance")
KINDS = {"message": "Xabar", "results": "Natijalar", "certificates": "Sertifikatlar"}


def _filters(form) -> dict:
    return {k: str(form.get(k, "")).strip() for k in FILTER_KEYS if str(form.get(k, "")).strip()}


async def _filters_with_round(session: AsyncSession, form) -> dict:
    """Davomat filtri tanlangan bo'lsa, joriy tur ID'sini ham saqlaymiz (keyin tur o'zgarsa ham to'g'ri qolsin)."""
    d = _filters(form)
    if d.get("attendance"):
        cur = await ranking.current_round(session)
        if cur:
            d["att_round"] = str(cur.id)
    return d


@router.get("")
async def page(request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    total = (await session.execute(select(func.count()).select_from(Broadcast))).scalar_one()
    pg = Page(total, int_or_none(params.get("page")) or 1, 20, params)
    items = (await session.execute(select(Broadcast).order_by(Broadcast.id.desc())
                                   .offset(pg.offset).limit(pg.per_page))).scalars().all()
    schools = (await session.execute(select(School).order_by(School.position, School.name))).scalars().all()
    grades = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    from ...bot import loader
    return render(request, "admin/broadcasts.html", {
        "items": items, "page": pg, "schools": schools, "grades": grades, "subjects": subjects,
        "rounds": rounds, "kinds": KINDS, "bot_ok": loader.bot is not None,
        "any_active": any(b.status in ("sending", "scheduled") for b in items),
        "loads": json.loads})


@router.get("/table")
async def table_partial(request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    """Holatni avtomatik yangilash uchun faqat jadval (HTMX)."""
    items = (await session.execute(select(Broadcast).order_by(Broadcast.id.desc()).limit(20))).scalars().all()
    return render(request, "admin/_broadcast_table.html", {
        "items": items, "kinds": KINDS, "loads": json.loads,
        "any_active": any(b.status in ("sending", "scheduled") for b in items)})


@router.post("/count", response_class=HTMLResponse)
async def count(request: Request, user: User = Depends(require_admin),
                session: AsyncSession = Depends(get_session)):
    form = await request.form()
    b = Broadcast(filters_json=json.dumps(await _filters_with_round(session, form)))
    n = await bsvc.count_recipients(b)
    return HTMLResponse(f"<b>{n}</b> ta qabul qiluvchi")


@router.post("/create")
async def create(request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    form = await request.form()
    kind = str(form.get("kind", "message"))
    if kind not in KINDS:
        raise HTTPException(400)
    text = str(form.get("text", "")).replace("\r\n", "\n").strip()
    text_cyr = str(form.get("text_cyr", "")).replace("\r\n", "\n").strip()
    errors = []
    if kind == "message" and not text:
        errors.append("Xabar matni bo'sh")
    if len(text) > 4000 or len(text_cyr) > 4000:
        errors.append("Matn 4000 belgidan oshmasin")
    try:
        when = parse_local_input(str(form.get("scheduled_at", ""))) or now_utc()
    except ValueError:
        errors.append("Vaqt noto'g'ri")
        when = now_utc()
    photo = form.get("photo")
    photo_path = ""
    if isinstance(photo, UploadFile) and photo.filename:
        data = await photo.read()
        if len(data) > 10 * 1024 * 1024:
            errors.append("Rasm 10 MB dan oshmasin")
        elif not (photo.content_type or "").startswith("image/"):
            errors.append("Faqat rasm fayli yuklang")
        else:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", photo.filename)[-60:]
            path = config.UPLOAD_DIR / "broadcasts" / f"{datetime.now():%Y%m%d%H%M%S}_{safe}"
            path.write_bytes(data)
            photo_path = str(path)
        if photo_path and len(text) > 1024:
            errors.append("Rasmli xabar matni 1024 belgidan oshmasin (Telegram cheklovi)")
    if kind == "results" and not st.get("results_published"):
        errors.append("Avval natijalarni e'lon qiling (Reyting sahifasida)")
    if kind == "certificates" and not (st.get("results_published") and st.get("certificates_enabled")):
        errors.append("Natijalar e'lon qilingan va sertifikatlar yoqilgan bo'lishi kerak")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse("/admin/broadcasts", 303)
    b = Broadcast(kind=kind, text=text, text_cyr=text_cyr, photo_path=photo_path,
                  filters_json=json.dumps(await _filters_with_round(session, form)), scheduled_at=when,
                  round_id=int_or_none(form.get("round_id")), created_by=user.username)
    session.add(b)
    await session.flush()
    audit.log(session, user, "create", "broadcast", b.id, f"{KINDS[kind]}: {text[:100]}", ip=client_ip(request))
    await session.commit()
    bsvc.wake()
    flash(request, "Xabar navbatga qo'yildi" + (" va rejalashtirildi" if when > now_utc() else ""))
    return RedirectResponse("/admin/broadcasts", 303)


@router.post("/quick/{kind}")
async def quick(kind: str, request: Request, user: User = Depends(require_admin),
                session: AsyncSession = Depends(get_session)):
    """Reyting sahifasidan: natijalar yoki sertifikatlarni hammaga yuborish."""
    if kind not in ("results", "certificates"):
        raise HTTPException(404)
    if not st.get("results_published") or (kind == "certificates" and not st.get("certificates_enabled")):
        flash(request, "Avval natijalarni e'lon qiling" + (" va sertifikatlarni yoqing" if kind == "certificates" else ""), "error")
        return RedirectResponse("/admin/ranking", 303)
    b = Broadcast(kind=kind, filters_json="{}", created_by=user.username, scheduled_at=now_utc())
    session.add(b)
    await session.flush()
    audit.log(session, user, "create", "broadcast", b.id, KINDS[kind], ip=client_ip(request))
    await session.commit()
    bsvc.wake()
    flash(request, f"{KINDS[kind]} yuborish boshlandi. Jarayonni shu sahifada kuzating.")
    return RedirectResponse("/admin/broadcasts", 303)


@router.post("/{bid}/cancel")
async def cancel(bid: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    b = await session.get(Broadcast, bid)
    if b is None:
        raise HTTPException(404)
    if b.status in ("scheduled", "sending"):
        b.status = "cancelled"
        b.finished_at = now_utc()
        audit.log(session, user, "cancel", "broadcast", bid, "Bekor qilindi", ip=client_ip(request))
        await session.commit()
        flash(request, "Xabar to'xtatildi")
    return RedirectResponse("/admin/broadcasts", 303)

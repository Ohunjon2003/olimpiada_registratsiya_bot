"""Admin: ishtirokchilar ro'yxati, filtrlar, Excel eksport, ko'rish va tahrirlash."""
from __future__ import annotations

import asyncio
from datetime import datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import config
from ...db import get_session
from ...models import (CustomQuestion, FsmRecord, Grade, Participant, Result,
                       Round, School, Subject, User)
from ...services import audit, ranking
from ...services import certificates as certs
from ...services import design as dz
from ...services import seating
from ...services import export
from ...services import attendance as att_svc
from ...services.participants import (ParticipantFilter, apply_filter,
                                      build_search_text, resolve_att_round)
from ...utils import clean_name, normalize_phone
from ..core import (Page, client_ip, flash, int_or_none, render,
                    require_admin, verify_csrf)

router = APIRouter(prefix="/admin/participants", dependencies=[Depends(verify_csrf)])


async def _lookups(session):
    schools = (await session.execute(select(School).order_by(School.position, School.name))).scalars().all()
    grades = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    return schools, grades, subjects


SORTS = {
    "new": Participant.id.desc(),
    "old": Participant.id.asc(),
    "name": Participant.last_name.asc(),
    "school": Participant.school_id.asc(),
    "grade": Participant.grade_id.asc(),
}


@router.get("")
async def list_view(request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    f = ParticipantFilter.from_params(params)
    await resolve_att_round(session, f)
    q = apply_filter(select(Participant), f)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    per_page = int_or_none(params.get("per_page")) or 50
    per_page = per_page if per_page in (25, 50, 100, 200) else 50
    page = Page(total, int_or_none(params.get("page")) or 1, per_page, params)
    sort = params.get("sort") if params.get("sort") in SORTS else "new"
    rows = (await session.execute(q.order_by(SORTS[sort], Participant.id.desc())
                                  .offset(page.offset).limit(page.per_page))).scalars().all()
    schools, grades, subjects = await _lookups(session)
    export_qs = urlencode({k: v for k, v in f.as_dict().items() if v})
    cur = await ranking.current_round(session)
    att = await att_svc.status_map(session, cur.id, [p.id for p in rows]) if cur else {}
    return render(request, "admin/participants.html", {
        "rows": rows, "page": page, "f": f, "params": params, "sort": sort, "att": att, "round": cur,
        "schools": schools, "grades": grades, "subjects": subjects, "export_qs": export_qs})


@router.get("/export")
async def export_xlsx(request: Request, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    f = ParticipantFilter.from_params(dict(request.query_params))
    await resolve_att_round(session, f)
    rows = (await session.execute(apply_filter(select(Participant), f)
                                  .order_by(Participant.last_name, Participant.first_name))).scalars().all()
    questions = (await session.execute(select(CustomQuestion).order_by(CustomQuestion.position,
                                                                       CustomQuestion.id))).scalars().all()
    cur = await ranking.current_round(session)
    att = await att_svc.status_map(session, cur.id) if cur else None
    data = await asyncio.to_thread(export.participants_xlsx, rows, questions, att)
    name = f"ishtirokchilar_{datetime.now(config.TZ):%Y%m%d_%H%M}.xlsx"
    audit.log(session, user, "export", "participant", "", f"{len(rows)} ta ishtirokchi eksport qilindi",
              ip=client_ip(request))
    await session.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{pid}")
async def detail(pid: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    p = await session.get(Participant, pid)
    if p is None:
        raise HTTPException(404, "Ishtirokchi topilmadi")
    results = (await session.execute(select(Result, Subject, Round)
                                     .join(Subject, Subject.id == Result.subject_id)
                                     .join(Round, Round.id == Result.round_id)
                                     .where(Result.participant_id == pid)
                                     .order_by(Round.position, Subject.position))).all()
    schools, grades, subjects = await _lookups(session)
    cert_items = await certs.certificates_for(session, p)
    seat = await seating.current_assignment(session, p)
    from ...models import SheetScan
    scans = (await session.execute(select(SheetScan).where(
        (SheetScan.participant_id == pid) | (SheetScan.qr_participant_id == pid)).order_by(SheetScan.id.desc()))).scalars().all()
    return render(request, "admin/participant.html", {
        "p": p, "results": results, "schools": schools, "grades": grades, "subjects": subjects,
        "cert_items": cert_items, "errors": [], "seat": seat, "scans": scans,
        "seat_text": seating.seat_text(seat.room, seat) if seat else ""})


@router.post("/{pid}/save")
async def save(pid: int, request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    p = await session.get(Participant, pid)
    if p is None:
        raise HTTPException(404)
    form = await request.form()
    errors = []
    first, last = clean_name(str(form.get("first_name", ""))), clean_name(str(form.get("last_name", "")))
    phone = normalize_phone(str(form.get("phone", "")))
    if not first:
        errors.append("Ism noto'g'ri")
    if not last:
        errors.append("Familiya noto'g'ri")
    if not phone:
        errors.append("Telefon noto'g'ri (+998XXXXXXXXX)")
    elif (await session.execute(select(func.count()).select_from(Participant).where(
            Participant.phone == phone, Participant.id != pid))).scalar_one():
        errors.append("Bu telefon raqam boshqa ishtirokchida bor")
    school = await session.get(School, int_or_none(form.get("school_id")) or 0)
    grade = await session.get(Grade, int_or_none(form.get("grade_id")) or 0)
    if not school:
        errors.append("Maktab tanlanmagan")
    if not grade:
        errors.append("Sinf tanlanmagan")
    sids = [int(x) for x in form.getlist("subjects") if str(x).isdigit()]
    new_subjects = list((await session.execute(select(Subject).where(Subject.id.in_(sids)))).scalars().all()) if sids else []
    if not new_subjects:
        errors.append("Kamida bitta fan tanlang")
    # Natijasi bor fanni olib tashlashdan himoya
    removed = {s.id for s in p.subjects} - {s.id for s in new_subjects}
    if removed:
        has = (await session.execute(select(Subject.name).join(Result, Result.subject_id == Subject.id)
                                     .where(Result.participant_id == pid, Subject.id.in_(removed)))).scalars().all()
        if has:
            errors.append(f"Natijasi kiritilgan fanni olib tashlab bo'lmaydi: {', '.join(set(has))}. "
                          f"Avval natijani o'chiring.")
    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(f"/admin/participants/{pid}", 303)

    old = {"first_name": p.first_name, "last_name": p.last_name, "phone": p.phone, "school": p.school_id,
           "grade": p.grade_id, "address": p.address, "status": p.status,
           "subjects": [s.id for s in p.subjects]}
    p.first_name, p.last_name, p.phone = first, last, phone
    p.school_id, p.grade_id = school.id, grade.id
    p.address = str(form.get("address", "")).strip()[:256]
    p.instagram_username = str(form.get("instagram_username", "")).strip().lstrip("@")[:64]
    p.status = "disqualified" if form.get("status") == "disqualified" else "active"
    p.note = str(form.get("note", "")).strip()
    p.subjects = new_subjects
    p.search_text = build_search_text(p)
    new = {"first_name": first, "last_name": last, "phone": phone, "school": school.id, "grade": grade.id,
           "address": p.address, "status": p.status, "subjects": [s.id for s in new_subjects]}
    audit.log(session, user, "update", "participant", pid, f"{p.code} {p.full_name}",
              old=old, new=new, ip=client_ip(request))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        flash(request, "Saqlab bo'lmadi: telefon raqam band", "error")
        return RedirectResponse(f"/admin/participants/{pid}", 303)
    ranking.invalidate()
    flash(request, "Ishtirokchi ma'lumotlari saqlandi")
    return RedirectResponse(f"/admin/participants/{pid}", 303)


@router.post("/{pid}/delete")
async def delete(pid: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    p = await session.get(Participant, pid)
    if p is None:
        raise HTTPException(404)
    snap = {"code": p.code, "name": p.full_name, "phone": p.phone, "telegram_id": p.telegram_id}
    await session.delete(p)
    audit.log(session, user, "delete", "participant", pid, f"{snap['code']} {snap['name']} o'chirildi",
              old=snap, ip=client_ip(request))
    await session.commit()
    ranking.invalidate()
    # Bot holatini ham tozalaymiz — o'quvchi qaytadan ro'yxatdan o'ta olsin
    await session.execute(sql_delete(FsmRecord).where(FsmRecord.key.like(f"%:{snap['telegram_id']}:%")))
    await session.commit()
    flash(request, "Ishtirokchi o'chirildi. U botda qaytadan ro'yxatdan o'tishi mumkin.")
    return RedirectResponse("/admin/participants", 303)


@router.post("/{pid}/message")
async def send_message(pid: int, request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    """Bitta ishtirokchiga bot orqali xabar."""
    from ...bot import loader
    p = await session.get(Participant, pid)
    if p is None:
        raise HTTPException(404)
    form = await request.form()
    text = str(form.get("text", "")).strip()
    if not text:
        flash(request, "Xabar matni bo'sh", "error")
    elif loader.bot is None:
        flash(request, "Bot ishlamayapti", "error")
    else:
        try:
            await loader.bot.send_message(p.telegram_id, text[:4000], parse_mode=None)
            audit.log(session, user, "message", "participant", pid, text[:200], ip=client_ip(request))
            await session.commit()
            flash(request, "Xabar yuborildi")
        except Exception as e:
            flash(request, f"Yuborib bo'lmadi: {e}", "error")
    return RedirectResponse(f"/admin/participants/{pid}", 303)


@router.get("/{pid}/certificate/{index}")
async def certificate(pid: int, index: int, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    p = await session.get(Participant, pid)
    if p is None:
        raise HTTPException(404)
    items = await certs.certificates_for(session, p)
    if not 0 <= index < len(items):
        raise HTTPException(404)
    it = items[index]
    pdf = await asyncio.to_thread(lambda: dz.to_pdf(it.render(), certs.template_dpi(it.template)))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{it.filename}"'})

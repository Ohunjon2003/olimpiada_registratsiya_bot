"""Avtomatik tekshiruv: skaner (hakam/admin telefonida), kalitlar, titullarni chop etish,
tekshirish navbati, ishlar ro'yxati va o'quvchilarga botda yuborish."""
from __future__ import annotations

import asyncio
import json
import secrets
from io import BytesIO
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ...db import get_session, session_scope
from ...models import (AnswerKey, Broadcast, ExamSession, Grade, Participant, Round, School,
                       SheetScan, Subject, User)
from ...services import audit, jobs, omr, omr_service as osvc, ranking
from ...services import broadcast as bsvc
from ...services import settings as st
from ...services.pdfwriter import JpegPdfWriter
from ...utils import now_utc, search_norm
from ..core import (Page, client_ip, current_user, flash, int_or_none, render,
                    require_admin, verify_csrf)

router = APIRouter(dependencies=[Depends(verify_csrf)])
MAX_IMAGE = 12 * 1024 * 1024
_user_locks: dict[int, asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# Skaner (hakam va admin)
# ---------------------------------------------------------------------------
@router.get("/scan")
async def scan_page(request: Request, user: User = Depends(current_user),
                    session: AsyncSession = Depends(get_session)):
    subjects = (await session.execute(select(Subject).where(Subject.omr_enabled == True)  # noqa: E712
                                      .order_by(Subject.position, Subject.id))).scalars().all()
    if not user.is_admin:
        mine = {s.id for s in user.subjects}
        subjects = [s for s in subjects if s.id in mine]
    today = (await session.execute(select(func.count()).select_from(SheetScan).where(
        SheetScan.scanned_by == user.username))).scalar_one()
    return render(request, "omr/scan.html", {"subjects": subjects, "my_count": today})


@router.post("/scan/frame")
async def scan_frame(request: Request, user: User = Depends(current_user),
                     session: AsyncSession = Depends(get_session)):
    """Kameradan kadr yoki surat. Javob JSON: state, message (sahifa rangli xabar ko'rsatadi)."""
    form = await request.form()
    up = form.get("image")
    if not isinstance(up, UploadFile):
        raise HTTPException(400, "Rasm yuborilmadi")
    data = await up.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        return JSONResponse({"state": "error", "message": "Rasm juda katta (12 MB dan oshmasin)"})
    lock = _user_locks.setdefault(user.id, asyncio.Lock())
    if lock.locked():  # bitta foydalanuvchidan bir vaqtda bitta kadr — server yuklanib qolmasin
        return JSONResponse({"state": "busy", "message": ""})
    async with lock:
        try:
            rep = await osvc.process(session, user, data, force=bool(form.get("force")))
        except Exception as e:  # kutilmagan xato — sahifa to'xtab qolmasin
            import logging
            logging.getLogger("omr").exception("Skaner xatosi")
            return JSONResponse({"state": "error", "message": f"Serverda xato: {type(e).__name__}"})
    return JSONResponse(rep.as_dict())


# ---------------------------------------------------------------------------
# Admin: sozlash, kalitlar
# ---------------------------------------------------------------------------
async def _round(session: AsyncSession, round_id: Optional[int]) -> Round:
    if round_id:
        r = await session.get(Round, round_id)
        if r:
            return r
    cur = await ranking.current_round(session)
    if cur is None:
        raise HTTPException(400, "Tur yaratilmagan")
    return cur


@router.get("/admin/omr")
async def admin_page(request: Request, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    tab = params.get("tab") if params.get("tab") in ("setup", "print", "review", "scans") else "setup"
    round_ = await _round(session, int_or_none(params.get("round_id")))
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    keys = {}
    for k in (await session.execute(select(AnswerKey).where(AnswerKey.round_id == round_.id))).scalars():
        keys[(k.subject_id, k.variant)] = k
    counts = {}
    for sid, status, n in (await session.execute(
            select(SheetScan.subject_id, SheetScan.status, func.count()).where(SheetScan.round_id == round_.id)
            .group_by(SheetScan.subject_id, SheetScan.status))).all():
        counts[(sid, status)] = n
    takers = {}
    for s in subjects:
        from ...models import participant_subjects
        takers[s.id] = (await session.execute(select(func.count()).select_from(participant_subjects).join(
            Participant, Participant.id == participant_subjects.c.participant_id).where(
            participant_subjects.c.subject_id == s.id, ranking.eligible_condition(round_)))).scalar_one()
    review_n = sum(n for (sid, stt), n in counts.items() if stt == "review")
    ctx = {"tab": tab, "round": round_, "rounds": rounds, "subjects": subjects, "keys": keys, "counts": counts,
           "takers": takers, "review_n": review_n, "ready": {s.id: osvc.omr_ready(s) for s in subjects},
           "letters": omr.OPTIONS, "params": params, "reasons": osvc.REASONS, "statuses": osvc.STATUS}
    if tab == "print":
        ctx["exam_sessions"] = (await session.execute(select(ExamSession).where(ExamSession.round_id == round_.id)
                                                      .order_by(ExamSession.day, ExamSession.start_time))).scalars().all()
    if tab == "review":
        ctx["items"] = (await session.execute(select(SheetScan).where(SheetScan.round_id == round_.id,
                                                                      SheetScan.status == "review")
                                              .order_by(SheetScan.id).limit(200))).scalars().all()
        codes = [x.id_marked for x in ctx["items"] if x.id_marked.isdigit()]
        ctx["marked_people"] = {p.code: p for p in (await session.execute(select(Participant).where(
            Participant.code.in_(codes or ["-"])))).scalars()}
    if tab == "scans":
        q = select(SheetScan).join(Participant, Participant.id == SheetScan.participant_id).where(
            SheetScan.round_id == round_.id)
        if params.get("subject_id", "").isdigit():
            q = q.where(SheetScan.subject_id == int(params["subject_id"]))
        if params.get("school_id", "").isdigit():
            q = q.where(Participant.school_id == int(params["school_id"]))
        if params.get("grade_id", "").isdigit():
            q = q.where(Participant.grade_id == int(params["grade_id"]))
        status = params.get("status") or "applied"
        if status in osvc.STATUS:
            q = q.where(SheetScan.status == status)
        if params.get("sent") == "no":
            q = q.where(SheetScan.sent_at.is_(None))
        for key, op in (("min", ">="), ("max", "<=")):
            try:
                v = float(params.get(key, "").replace(",", "."))
                q = q.where(SheetScan.percent >= v if op == ">=" else SheetScan.percent <= v)
            except ValueError:
                pass
        if params.get("q"):
            needle = search_norm(params["q"])
            q = q.where(or_(Participant.search_text.contains(needle, autoescape=True)))
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
        pg = Page(total, int_or_none(params.get("page")) or 1, 50, params)
        ctx["items"] = (await session.execute(q.order_by(SheetScan.id.desc()).offset(pg.offset).limit(pg.per_page))).scalars().all()
        ctx["page"] = pg
        ctx["schools"] = (await session.execute(select(School).order_by(School.position, School.name))).scalars().all()
        ctx["grades"] = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
        # Filtrdagilarning hammasi (botga yuborish uchun)
        sub = q.subquery()
        ctx["all_pids"] = ",".join(str(x) for x in sorted(set((await session.execute(
            select(sub.c.participant_id))).scalars().all()))) if total <= 3000 else ""
    return render(request, "omr/admin.html", ctx)


@router.post("/admin/omr/settings")
async def omr_settings(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    form = await request.form()
    await st.set_many(session, {"omr_id_required": bool(form.get("omr_id_required")),
                                "omr_show_name": bool(form.get("omr_show_name"))})
    flash(request, "Sozlamalar saqlandi")
    return RedirectResponse("/admin/omr?tab=setup", 303)


@router.post("/admin/omr/subject/{sid}")
async def subject_toggle(sid: int, request: Request, user: User = Depends(require_admin),
                         session: AsyncSession = Depends(get_session)):
    subject = await session.get(Subject, sid)
    if subject is None:
        raise HTTPException(404)
    form = await request.form()
    enable = bool(form.get("omr_enabled"))
    if enable and (err := osvc.omr_ready(subject)):
        flash(request, f"{subject.name}: {err}", "error")
        return RedirectResponse("/admin/omr?tab=setup", 303)
    variants = max(1, min(4, int_or_none(form.get("variants")) or 1))
    subject.omr_enabled, subject.variants = enable, variants
    audit.log(session, user, "update", "subject", sid,
              f"{subject.name}: avtomatik tekshiruv {'yoqildi' if enable else 'o`chirildi'}, variantlar: {variants}",
              ip=client_ip(request))
    await session.commit()
    flash(request, f"{subject.name}: saqlandi")
    return RedirectResponse("/admin/omr?tab=setup", 303)


@router.post("/admin/omr/key/{sid}")
async def save_key(sid: int, request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    subject = await session.get(Subject, sid)
    if subject is None:
        raise HTTPException(404)
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    n = subject.question_total
    changed = []
    for v in range(subject.variants):
        raw = str(form.get(f"key_{v}") or "")
        if not raw.strip():
            continue
        key, err = omr.clean_key(raw, n)
        if err:
            flash(request, f"{subject.name}, {omr.variant_letter(v)} variant: {err}", "error")
            return RedirectResponse(f"/admin/omr?tab=setup&round_id={round_.id}#s{sid}", 303)
        row = (await session.execute(select(AnswerKey).where(AnswerKey.subject_id == sid, AnswerKey.round_id == round_.id,
                                                             AnswerKey.variant == v))).scalars().first()
        if row is None:
            row = AnswerKey(subject_id=sid, round_id=round_.id, variant=v)
            session.add(row)
        if row.answers != key:
            changed.append(omr.variant_letter(v))
            audit.log(session, user, "update", "answer_key", f"{sid}/{round_.id}/{v}",
                      f"{subject.name}: {omr.variant_letter(v)} variant kaliti", old=row.answers or None, new=key,
                      ip=client_ip(request))
        row.answers, row.updated_by = key, user.username
    await session.flush()
    done, skipped = await osvc.regrade(session, subject, round_.id, user)
    await session.commit()
    msg = f"{subject.name}: kalit saqlandi" + (f" ({', '.join(changed)})" if changed else " (o'zgarish yo'q)")
    if done:
        msg += f". {done} ta ish qayta hisoblandi"
    if skipped:
        msg += f". {skipped} ta natija qo'lda o'zgartirilgani uchun tegilmadi"
    flash(request, msg)
    return RedirectResponse(f"/admin/omr?tab=setup&round_id={round_.id}#s{sid}", 303)


@router.get("/admin/omr/sample/{sid}.png")
async def sample(sid: int, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    subject = await session.get(Subject, sid)
    if subject is None or osvc.omr_ready(subject):
        raise HTTPException(404)
    n = subject.question_total
    info = omr.CardInfo(omr.make_token(0, sid, 0, 0, n, 4), st.get("olympiad_name"), subject.name, "0017",
                        "101-xona · 14-o'rin", "B variant" if subject.variants > 1 else "",
                        "Aliyev Aziz" if st.get("omr_show_name") else "")
    img = await asyncio.to_thread(omr.render_card, omr.layout(n, 4), info, 150)
    buf = BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), media_type="image/png")


@router.post("/admin/omr/print")
async def print_cards(request: Request, user: User = Depends(require_admin)):
    form = await request.form()
    sid, rid, ses = int_or_none(form.get("subject_id")), int_or_none(form.get("round_id")), int_or_none(form.get("session_id"))
    who, ip = SimpleNamespace(id=user.id, username=user.username), client_ip(request)

    async def worker(job: jobs.Job) -> None:
        async with session_scope() as s:
            subject = await s.get(Subject, sid or 0)
            if subject is None or not subject.omr_enabled:
                job.error = "Fan tanlanmagan yoki avtomatik tekshiruv yoqilmagan"
                return
            round_ = await _round(s, rid)
            items = await osvc.cards(s, subject, round_, ses)
            job.total = len(items)
            if not items:
                job.error = "Bu fanni tanlagan o'quvchi yo'q"
                return
            kind = items[0][1].kind
            per = omr.KINDS[kind][5]
            path = jobs.exports_dir() / f"titullar_{secrets.token_hex(4)}.pdf"
            writer = JpegPdfWriter(path, 200, quality=92)
            try:
                for i in range(0, len(items), per):
                    chunk = items[i:i + per]
                    cards = [await asyncio.to_thread(omr.render_card, lay, info, 200) for info, lay in chunk]
                    for page in await asyncio.to_thread(omr.compose_pages, cards, kind, 200):
                        await asyncio.to_thread(writer.add, page)
                    job.done += len(chunk)
            finally:
                writer.close()
            job.path, job.filename, job.media_type = path, f"titullar_{subject.name}.pdf", "application/pdf"
            audit.log(s, who, "export", "omr", sid, f"{subject.name}: {len(items)} ta titul chop etishga", ip=ip)
            await s.commit()

    job = jobs.start("Titullar", user.id, worker)
    return HTMLResponse(_job_html(job))


def _job_html(job: jobs.Job) -> str:
    from html import escape
    if job.status == "running":
        return (f'<div class="job" hx-get="/admin/omr/jobs/{job.id}" hx-trigger="every 2s" hx-swap="outerHTML">'
                f'⏳ Tayyorlanmoqda: <b>{job.done}</b> / {job.total or "…"} titul'
                f'<div class="bar"><span style="width:{job.percent}%"></span></div></div>')
    if job.status == "done":
        return (f'<div class="job ok">✅ Tayyor: {job.done} ta titul. '
                f'<a class="btn sm primary" href="/admin/certificates/jobs/{job.id}/download">⬇️ PDF yuklab olish</a></div>')
    return f'<div class="job err">⚠️ {escape(job.error)}</div>'


@router.get("/admin/omr/jobs/{job_id}")
async def job_status(job_id: str, user: User = Depends(require_admin)):
    job = jobs.get(job_id, user.id)
    return HTMLResponse(_job_html(job) if job else '<div class="job err">Ish topilmadi</div>')


# ---------------------------------------------------------------------------
# Bitta ish: ko'rish, qaror, rasm
# ---------------------------------------------------------------------------
async def _scan(session: AsyncSession, scan_id: int) -> SheetScan:
    sc = await session.get(SheetScan, scan_id)
    if sc is None:
        raise HTTPException(404, "Ish topilmadi")
    return sc


@router.get("/admin/omr/scan/{scan_id}")
async def scan_view(scan_id: int, request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    sc = await _scan(session, scan_id)
    keys = await osvc.keys_for(session, sc.subject_id, sc.round_id)
    key = keys.get(sc.variant, "")
    history = (await session.execute(select(SheetScan).where(
        or_(SheetScan.participant_id == sc.participant_id, SheetScan.qr_participant_id == sc.qr_participant_id),
        SheetScan.subject_id == sc.subject_id, SheetScan.round_id == sc.round_id).order_by(SheetScan.id))).scalars().all()
    qr_p = await session.get(Participant, sc.qr_participant_id)
    marked_p = (await session.execute(select(Participant).where(Participant.code == sc.id_marked))).scalars().first() \
        if sc.id_marked.isdigit() else None
    rows = [(i + 1, a, key[i] if i < len(key) else "") for i, a in enumerate(sc.answers)]
    return render(request, "omr/scan_view.html", {
        "sc": sc, "key": key, "rows": rows, "history": history, "qr_p": qr_p, "marked_p": marked_p,
        "reasons": osvc.REASONS, "statuses": osvc.STATUS, "variant": omr.variant_letter(sc.variant)})


@router.get("/admin/omr/scan/{scan_id}/{what}.jpg")
async def scan_image(scan_id: int, what: str, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    sc = await _scan(session, scan_id)
    if what == "raw":
        p = osvc.scan_path(sc.raw_image)
        if p is None:
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")
    keys = await osvc.keys_for(session, sc.subject_id, sc.round_id)
    try:
        img = await asyncio.to_thread(osvc.render_scan, sc, keys.get(sc.variant))
    except FileNotFoundError:
        raise HTTPException(404, "Rasm topilmadi")
    buf = BytesIO()
    img.save(buf, "JPEG", quality=88)
    return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/admin/omr/scan/{scan_id}/resolve")
async def scan_resolve(scan_id: int, request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    sc = await _scan(session, scan_id)
    form = await request.form()
    action = str(form.get("action") or "")
    if action not in ("accept", "accept_marked", "reject"):
        raise HTTPException(400)
    answers = str(form.get("answers")) if form.get("answers") is not None and str(form.get("answers")).strip() else None
    try:
        msg = await osvc.resolve(session, sc, action, user, answers, str(form.get("code") or ""))
        flash(request, msg)
    except ValueError as e:
        await session.rollback()
        flash(request, str(e), "error")
    back = str(form.get("back") or f"/admin/omr/scan/{scan_id}")
    return RedirectResponse(back if back.startswith("/admin/") else f"/admin/omr/scan/{scan_id}", 303)


@router.post("/admin/omr/review/accept-empty")
async def accept_empty(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    """«ID belgilanmagan» ishlarni QR bo'yicha birdaniga tasdiqlash."""
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    items = (await session.execute(select(SheetScan).where(SheetScan.round_id == round_.id, SheetScan.status == "review",
                                                           SheetScan.reason == "id_empty"))).scalars().all()
    ok = 0
    for sc in items:
        try:
            await osvc.resolve(session, sc, "accept", user)
            ok += 1
        except ValueError:
            await session.rollback()
    flash(request, f"{ok} ta ish QR bo'yicha tasdiqlandi")
    return RedirectResponse(f"/admin/omr?tab=review&round_id={round_.id}", 303)


@router.post("/admin/omr/send")
async def send_sheets(request: Request, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    """Tekshirilgan titullarni o'quvchilarga botda yuborish (tanlanganlar yoki filtrdagilarning hammasi)."""
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    ids = form.getlist("pid") or [x for x in str(form.get("all_pids") or "").split(",") if x]
    ids = sorted({int(x) for x in ids if str(x).isdigit()})
    if not ids:
        flash(request, "Hech kim tanlanmagan", "error")
        return RedirectResponse(str(form.get("back") or "/admin/omr?tab=scans"), 303)
    flt = {"ids": ",".join(map(str, ids))}
    if str(form.get("subject_id") or "").isdigit():
        flt["sheet_subject"] = str(form.get("subject_id"))
    b = Broadcast(kind="sheets", round_id=round_.id, filters_json=json.dumps(flt), scheduled_at=now_utc(),
                  created_by=user.username, text=f"Tekshirilgan titullar: {len(ids)} o'quvchi")
    session.add(b)
    await session.flush()
    audit.log(session, user, "create", "broadcast", b.id, b.text, ip=client_ip(request))
    await session.commit()
    bsvc.wake()
    flash(request, f"{len(ids)} ta o'quvchiga tekshirilgan titul yuborilmoqda («📣 Xabar yuborish»da kuzating)")
    back = str(form.get("back") or "/admin/omr?tab=scans")
    return RedirectResponse(back if back.startswith("/admin/") else "/admin/omr?tab=scans", 303)

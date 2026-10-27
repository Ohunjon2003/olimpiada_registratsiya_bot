"""Admin: sertifikat/diplom shablonlari va vizual dizayner."""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ...db import get_session
from ...models import (Block, CertificateTemplate, Participant, Round, Subject,
                       User)
from ...services import audit
from ...services import certificates as certs
from ..core import client_ip, flash, int_or_none, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/certificates", dependencies=[Depends(verify_csrf)])


async def _lookups(session):
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    blocks = (await session.execute(select(Block).order_by(Block.position, Block.id))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    return subjects, blocks, rounds


@router.get("")
async def list_view(request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    items = (await session.execute(select(CertificateTemplate).order_by(
        CertificateTemplate.position, CertificateTemplate.id))).scalars().all()
    subjects, blocks, rounds = await _lookups(session)
    return render(request, "admin/certificates.html", {
        "items": items, "subjects": {s.id: s for s in subjects}, "blocks": {b.id: b for b in blocks},
        "rounds": {r.id: r for r in rounds}})


@router.post("/new")
async def create(request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    w, h = certs.image_size(None)
    pos = ((await session.execute(select(func.max(CertificateTemplate.position)))).scalar() or 0) + 1
    tpl = CertificateTemplate(name="Yangi shablon", is_active=False, width=w, height=h, position=pos,
                              fields_json=json.dumps(certs.DEFAULT_FIELDS, ensure_ascii=False))
    session.add(tpl)
    await session.flush()
    audit.log(session, user, "create", "certificate", tpl.id, tpl.name, ip=client_ip(request))
    await session.commit()
    return RedirectResponse(f"/admin/certificates/{tpl.id}", 303)


async def _get(session, tid: int) -> CertificateTemplate:
    tpl = await session.get(CertificateTemplate, tid)
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    return tpl


@router.get("/{tid}")
async def edit(tid: int, request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    subjects, blocks, rounds = await _lookups(session)
    return render(request, "admin/certificate_edit.html", {
        "tpl": tpl, "subjects": subjects, "blocks": blocks, "rounds": rounds,
        "fields": certs.load_fields(tpl), "placeholders": certs.PLACEHOLDERS,
        "fonts": list(certs.FONTS.keys())})


@router.get("/{tid}/background")
async def background(tid: int, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    path = Path(tpl.background_path) if tpl.background_path else None
    if not path or not path.exists():
        path = certs.default_background_path()
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@router.post("/{tid}/settings")
async def save_settings(tid: int, request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Shablon nomi bo'sh", "error")
        return RedirectResponse(f"/admin/certificates/{tid}", 303)
    tpl.name = name[:128]
    tpl.is_active = bool(form.get("is_active"))
    tpl.scope = "block" if form.get("scope") == "block" else "subject"
    tpl.subject_id = int_or_none(form.get("subject_id")) if tpl.scope == "subject" else None
    tpl.block_id = int_or_none(form.get("block_id")) if tpl.scope == "block" else None
    tpl.round_id = int_or_none(form.get("round_id"))
    tpl.rule = form.get("rule") if form.get("rule") in ("all", "top", "percent") else "all"
    tpl.top_n = max(1, int_or_none(form.get("top_n")) or 3)
    try:
        tpl.min_percent = max(0.0, min(100.0, float(str(form.get("min_percent") or 0).replace(",", "."))))
    except ValueError:
        tpl.min_percent = 0
    up = form.get("background")
    if isinstance(up, UploadFile) and up.filename:
        data = await up.read()
        if len(data) > 15 * 1024 * 1024:
            flash(request, "Rasm 15 MB dan oshmasin", "error")
            return RedirectResponse(f"/admin/certificates/{tid}", 303)
        try:
            old = tpl.background_path
            tpl.background_path, tpl.width, tpl.height = await asyncio.to_thread(certs.save_background, data, tpl.id)
            if old and old != tpl.background_path:
                Path(old).unlink(missing_ok=True)
        except Exception:
            flash(request, "Rasmni o'qib bo'lmadi. PNG yoki JPG yuklang.", "error")
            return RedirectResponse(f"/admin/certificates/{tid}", 303)
    if form.get("reset_background"):
        if tpl.background_path:
            Path(tpl.background_path).unlink(missing_ok=True)
        tpl.background_path = ""
        tpl.width, tpl.height = certs.image_size(None)
    audit.log(session, user, "update", "certificate", tid, tpl.name, ip=client_ip(request))
    await session.commit()
    flash(request, "Shablon sozlamalari saqlandi")
    return RedirectResponse(f"/admin/certificates/{tid}", 303)


def _clean_fields(raw) -> list[dict]:
    """Dizaynerdan kelgan maydonlarni tekshiradi."""
    if not isinstance(raw, list):
        raise ValueError
    out = []
    for f in raw[:40]:
        if not isinstance(f, dict):
            continue
        color = str(f.get("color", "#000000"))[:9]
        if not (color.startswith("#") and len(color) in (4, 7)):
            color = "#000000"
        out.append({
            "text": str(f.get("text", ""))[:300],
            "x": max(0.0, min(100.0, float(f.get("x", 50)))),
            "y": max(0.0, min(100.0, float(f.get("y", 50)))),
            "size": max(0.3, min(20.0, float(f.get("size", 3)))),
            "color": color,
            "align": f.get("align") if f.get("align") in ("left", "center", "right") else "center",
            "font": f.get("font") if f.get("font") in certs.FONTS else "sans",
            "max_width": max(0.0, min(100.0, float(f.get("max_width", 0) or 0))),
        })
    return out


@router.post("/{tid}/fields")
async def save_fields(tid: int, request: Request, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    try:
        fields = _clean_fields(await request.json())
    except (ValueError, TypeError):
        raise HTTPException(400, "Noto'g'ri ma'lumot")
    tpl.fields_json = json.dumps(fields, ensure_ascii=False)
    audit.log(session, user, "update", "certificate", tid, f"{tpl.name}: dizayn saqlandi", ip=client_ip(request))
    await session.commit()
    return JSONResponse({"ok": True})


@router.get("/{tid}/preview.png")
async def preview(tid: int, user: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    png = await asyncio.to_thread(lambda: certs.to_png(certs.render(tpl, certs.sample_context())))
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{tid}/sample.pdf")
async def sample_pdf(tid: int, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    pdf = await asyncio.to_thread(lambda: certs.to_pdf(certs.render(tpl, certs.sample_context())))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="namuna.pdf"'})


@router.get("/{tid}/zip")
async def zip_all(tid: int, user: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    """Shu shablon bo'yicha barcha mos sertifikatlarni ZIP qilib yuklab olish (chop etish uchun)."""
    tpl = await _get(session, tid)
    people = (await session.execute(select(Participant).where(Participant.status == "active")
                                    .order_by(Participant.id))).scalars().all()
    items = []
    for p in people:
        for it in await certs.certificates_for(session, p):
            if it.template.id == tpl.id:
                items.append(it)
    if not items:
        raise HTTPException(404, "Bu shablon bo'yicha mos sertifikat yo'q (shablon faolmi, natijalar kiritilganmi?)")
    if len(items) > 2000:
        raise HTTPException(400, "Juda ko'p sertifikat (2000+). Shablon shartini toraytiring.")

    def build() -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for it in items:
                z.writestr(it.filename, certs.to_pdf(certs.render(it.template, it.context)))
        return buf.getvalue()
    data = await asyncio.to_thread(build)
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="sertifikatlar_{tpl.id}.zip"'})


@router.post("/{tid}/delete")
async def delete(tid: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    if tpl.background_path:
        Path(tpl.background_path).unlink(missing_ok=True)
    await session.delete(tpl)
    audit.log(session, user, "delete", "certificate", tid, tpl.name, ip=client_ip(request))
    await session.commit()
    flash(request, "Shablon o'chirildi")
    return RedirectResponse("/admin/certificates", 303)




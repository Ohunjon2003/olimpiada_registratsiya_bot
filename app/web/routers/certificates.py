"""Admin: sertifikat/diplom va ruxsatnoma shablonlari, Canva'ga o'xshash dizayner,
o'rinlar bo'yicha ko'rib chiqish va ommaviy tayyorlash (ZIP / bitta PDF)."""
from __future__ import annotations

import asyncio
import io
import json
import re
import secrets
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ...db import get_session, session_scope
from ...models import (Block, CertificateTemplate, Participant, Round, Subject,
                       User)
from ...services import audit, jobs
from ...services import certificates as certs
from ...services import design as dz
from ...services import design_presets as presets
from ...services import settings as st
from ...services.pdfwriter import JpegPdfWriter
from ..core import client_ip, flash, int_or_none, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/certificates", dependencies=[Depends(verify_csrf)])

MAX_UPLOAD = 15 * 1024 * 1024
_FONT_FILE = re.compile(r"^[a-z0-9-]{3,40}\.(ttf|otf)$")


async def _lookups(session):
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    blocks = (await session.execute(select(Block).order_by(Block.position, Block.id))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    return subjects, blocks, rounds


async def _get(session, tid: int) -> CertificateTemplate:
    tpl = await session.get(CertificateTemplate, tid)
    if tpl is None:
        raise HTTPException(404, "Shablon topilmadi")
    return tpl


# ---------------------------------------------------------------------------
# Ro'yxat va yangi shablon
# ---------------------------------------------------------------------------
@router.get("")
async def list_view(request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    items = (await session.execute(select(CertificateTemplate).order_by(
        CertificateTemplate.position, CertificateTemplate.id))).scalars().all()
    subjects, blocks, rounds = await _lookups(session)
    return render(request, "admin/certificates.html", {
        "certs": [t for t in items if t.kind != "pass"], "passes": [t for t in items if t.kind == "pass"],
        "subjects": {s.id: s for s in subjects}, "blocks": {b.id: b for b in blocks},
        "rounds": {r.id: r for r in rounds}, "rules": certs.RULES})


@router.get("/new")
async def new_page(request: Request, kind: str = "certificate", user: User = Depends(require_admin)):
    kind = "pass" if kind == "pass" else "certificate"
    plist = presets.PASS_PRESETS if kind == "pass" else presets.CERT_PRESETS
    return render(request, "admin/certificate_new.html", {
        "kind": kind, "presets": [(k, v[0]) for k, v in plist.items()]})


_preset_png: dict[str, bytes] = {}


@router.get("/preset/{key}.png")
async def preset_png(key: str, user: User = Depends(require_admin)):
    if key not in {**presets.CERT_PRESETS, **presets.PASS_PRESETS}:
        raise HTTPException(404)
    if key not in _preset_png:
        kind = "pass" if key in presets.PASS_PRESETS else "certificate"
        _preset_png[key] = await asyncio.to_thread(
            lambda: dz.to_png(dz.render(presets.build(key), certs.sample_context(kind)), 520))
    return Response(_preset_png[key], media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@router.get("/preset/{key}.json")
async def preset_json(key: str, user: User = Depends(require_admin)):
    """Muharrirda «Tayyor dizayn» tanlanganda."""
    if key not in {**presets.CERT_PRESETS, **presets.PASS_PRESETS}:
        raise HTTPException(404)
    return JSONResponse(presets.build(key))


@router.post("/new")
async def create(request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    form = await request.form()
    kind = "pass" if form.get("kind") == "pass" else "certificate"
    tpl = certs.new_template(kind, str(form.get("preset") or ""))
    tpl.position = ((await session.execute(select(func.max(CertificateTemplate.position)))).scalar() or 0) + 1
    session.add(tpl)
    await session.flush()
    audit.log(session, user, "create", "certificate", tpl.id, tpl.name, ip=client_ip(request))
    await session.commit()
    return RedirectResponse(f"/admin/certificates/{tpl.id}", 303)


@router.post("/{tid}/duplicate")
async def duplicate(tid: int, request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    src = await _get(session, tid)
    tpl = CertificateTemplate(
        name=(src.name + " (nusxa)")[:128], kind=src.kind, is_active=False, design_json=src.design_json,
        width=src.width, height=src.height, scope=src.scope, subject_id=src.subject_id, block_id=src.block_id,
        round_id=src.round_id, rule=src.rule, top_n=src.top_n, min_percent=src.min_percent,
        position=((await session.execute(select(func.max(CertificateTemplate.position)))).scalar() or 0) + 1)
    session.add(tpl)
    await session.flush()
    audit.log(session, user, "create", "certificate", tpl.id, f"Nusxa: {src.name}", ip=client_ip(request))
    await session.commit()
    flash(request, "Shablondan nusxa olindi (nofaol holatda)")
    return RedirectResponse(f"/admin/certificates/{tpl.id}", 303)


# ---------------------------------------------------------------------------
# Dizayner
# ---------------------------------------------------------------------------
@router.get("/{tid:int}")
async def edit(tid: int, request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    subjects, blocks, rounds = await _lookups(session)
    ph = certs.PASS_PH if tpl.kind == "pass" else certs.CERT_PH
    sample = certs.sample_context(tpl.kind)
    plist = presets.PASS_PRESETS if tpl.kind == "pass" else presets.CERT_PRESETS
    people = (await session.execute(select(Participant).where(Participant.status == "active")
                                    .order_by(Participant.id).limit(1))).scalars().first()
    return render(request, "admin/certificate_edit.html", {
        "tpl": tpl, "subjects": subjects, "blocks": blocks, "rounds": rounds, "rules": certs.RULES,
        "design": certs.load_design(tpl),
        "placeholders": [{"key": k, "label": v[0], "sample": sample.get(k, v[1])} for k, v in ph.items()],
        "page_presets": [(k, v[0]) for k, v in dz.PAGE_PRESETS.items()],
        "presets": [(k, v[0]) for k, v in plist.items()], "has_people": people is not None})


_metrics_cache: dict[str, object] = {}


def _metrics_json() -> bytes:
    key = st.raw("custom_fonts")
    if _metrics_cache.get("key") != key:
        _metrics_cache["data"] = json.dumps(dz.font_metrics_payload(), separators=(",", ":")).encode()
        _metrics_cache["key"] = key
    return _metrics_cache["data"]  # type: ignore[return-value]


@router.get("/fonts.json")
async def fonts_json(user: User = Depends(require_admin)):
    data = await asyncio.to_thread(_metrics_json)
    return Response(data, media_type="application/json", headers={"Cache-Control": "no-cache"})


@router.post("/{tid}/design")
async def save_design(tid: int, request: Request, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    try:
        raw = await request.json()
        certs.save_design(tpl, raw)
    except (ValueError, TypeError):
        raise HTTPException(400, "Dizayn ma'lumoti noto'g'ri")
    # Har saqlashda jurnalga yozmaymiz (avtosaqlash tez-tez ishlaydi) — faqat 5 daqiqada bir marta
    last = request.session.get(f"dz_audit_{tid}", 0)
    now = int(asyncio.get_running_loop().time())
    if now - last > 300:
        audit.log(session, user, "update", "certificate", tid, f"{tpl.name}: dizayn o'zgartirildi", ip=client_ip(request))
        request.session[f"dz_audit_{tid}"] = now
    await session.commit()
    return JSONResponse({"ok": True, "w": tpl.width, "h": tpl.height})


@router.post("/{tid}/settings")
async def save_settings(tid: int, request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        flash(request, "Shablon nomi bo'sh", "error")
        return RedirectResponse(f"/admin/certificates/{tid}#settings", 303)
    tpl.name = name[:128]
    tpl.is_active = bool(form.get("is_active"))
    if tpl.kind == "certificate":
        tpl.scope = "block" if form.get("scope") == "block" else "subject"
        tpl.subject_id = int_or_none(form.get("subject_id")) if tpl.scope == "subject" else None
        tpl.block_id = int_or_none(form.get("block_id")) if tpl.scope == "block" else None
        tpl.round_id = int_or_none(form.get("round_id"))
        tpl.rule = form.get("rule") if form.get("rule") in certs.RULES else "all"
        tpl.top_n = max(1, min(100, int_or_none(form.get("top_n")) or 1))
        try:
            tpl.min_percent = max(0.0, min(100.0, float(str(form.get("min_percent") or 0).replace(",", "."))))
        except ValueError:
            tpl.min_percent = 0
    audit.log(session, user, "update", "certificate", tid, f"{tpl.name}: sozlamalar", ip=client_ip(request))
    await session.commit()
    flash(request, "Shablon sozlamalari saqlandi")
    return RedirectResponse(f"/admin/certificates/{tid}#settings", 303)


@router.post("/{tid}/asset")
async def upload_asset(tid: int, request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    """Rasm yuklash (fon, logo, muhr, imzo). Javob: {name, url, w, h}."""
    await _get(session, tid)
    form = await request.form()
    up = form.get("file")
    if not isinstance(up, UploadFile) or not up.filename:
        raise HTTPException(400, "Fayl tanlanmagan")
    data = await up.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "Rasm 15 MB dan oshmasin")
    try:
        name = await asyncio.to_thread(dz.import_asset, data)
    except Exception:
        raise HTTPException(400, "Rasmni o'qib bo'lmadi. PNG, JPG yoki WEBP yuklang.")
    w, h = dz.asset_size(name)
    return JSONResponse({"name": name, "url": f"/admin/certificates/asset/{name}", "w": w, "h": h})


@router.get("/asset/{name}")
async def asset(name: str, user: User = Depends(require_admin)):
    p = dz.asset_path(name)
    if p is None:
        raise HTTPException(404, "Rasm topilmadi")
    return FileResponse(p, media_type="image/png", headers={"Cache-Control": "private, max-age=31536000, immutable"})


# --- Shriftlar --------------------------------------------------------------
@router.post("/fonts/upload")
async def font_upload(request: Request, user: User = Depends(require_admin)):
    """O'z shriftingizni yuklash (.ttf/.otf). Qalin varianti ixtiyoriy."""
    form = await request.form()
    name = str(form.get("name") or "").strip()[:40]
    files = {}
    for field in ("regular", "bold"):
        up = form.get(field)
        if isinstance(up, UploadFile) and up.filename:
            data = await up.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                flash(request, "Shrift fayli 8 MB dan oshmasin", "error")
                return RedirectResponse("/admin/certificates#fonts", 303)
            files[field] = (data, Path(up.filename).suffix.lower())
    if "regular" not in files:
        flash(request, "Shrift faylini tanlang (.ttf yoki .otf)", "error")
        return RedirectResponse("/admin/certificates#fonts", 303)
    key = "u-" + secrets.token_hex(4)
    saved = {}
    try:
        from fontTools.ttLib import TTFont
        for field, (data, ext) in files.items():
            if ext not in (".ttf", ".otf"):
                raise ValueError("faqat .ttf yoki .otf")
            font = TTFont(io.BytesIO(data))
            fam = font["name"].getDebugName(1) or "Shrift"
            if not name:
                name = fam[:40]
            if "glyf" not in font and "CFF " not in font:
                raise ValueError("shrift fayli buzuq")
            fname = f"{key}-{field}{ext}"
            (dz._custom_font_dir()).mkdir(parents=True, exist_ok=True)
            (dz._custom_font_dir() / fname).write_bytes(data)
            saved[field] = fname
            # Pillow ham o'qiy olishini tekshiramiz
            dz.pil_font(dz._custom_font_dir() / fname, 20)
    except Exception as e:
        for f in saved.values():
            (dz._custom_font_dir() / f).unlink(missing_ok=True)
        flash(request, f"Shriftni o'qib bo'lmadi: {e}", "error")
        return RedirectResponse("/admin/certificates#fonts", 303)
    try:
        items = json.loads(st.raw("custom_fonts") or "[]")
    except ValueError:
        items = []
    items.append({"key": key, "name": name or "Shrift", "regular": saved["regular"], "bold": saved.get("bold")})
    async with session_scope() as s:
        await st.set_many(s, {"custom_fonts": json.dumps(items, ensure_ascii=False)})
        audit.log(s, user, "create", "font", key, f"Shrift yuklandi: {name}", ip=client_ip(request))
        await s.commit()
    flash(request, f"«{name}» shrifti qo'shildi — dizaynerda «Yuklangan» bo'limida")
    return RedirectResponse("/admin/certificates#fonts", 303)


@router.post("/fonts/{key}/delete")
async def font_delete(key: str, request: Request, user: User = Depends(require_admin)):
    try:
        items = json.loads(st.raw("custom_fonts") or "[]")
    except ValueError:
        items = []
    keep, removed = [], None
    for it in items:
        if it.get("key") == key:
            removed = it
        else:
            keep.append(it)
    if removed:
        for f in (removed.get("regular"), removed.get("bold")):
            if f and _FONT_FILE.match(f):
                (dz._custom_font_dir() / f).unlink(missing_ok=True)
        async with session_scope() as s:
            await st.set_many(s, {"custom_fonts": json.dumps(keep, ensure_ascii=False)})
            audit.log(s, user, "delete", "font", key, f"Shrift o'chirildi: {removed.get('name')}", ip=client_ip(request))
            await s.commit()
        flash(request, "Shrift o'chirildi. Uni ishlatgan matnlar DejaVu Sans bilan chiqadi.")
    return RedirectResponse("/admin/certificates#fonts", 303)


@router.get("/fontfile/{name}")
async def font_file(name: str, user: User = Depends(require_admin)):
    if not _FONT_FILE.match(name):
        raise HTTPException(404)
    p = dz._custom_font_dir() / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="font/ttf" if name.endswith(".ttf") else "font/otf",
                        headers={"Cache-Control": "private, max-age=86400"})


# --- Ko'rinish --------------------------------------------------------------
async def _context_for(session, tpl: CertificateTemplate, code: str | None) -> dict:
    """Haqiqiy o'quvchi ma'lumoti bilan (ID raqami berilsa), aks holda namuna."""
    code = (code or "").strip()
    if code:
        p = (await session.execute(select(Participant).where(
            Participant.code == (code.zfill(4) if code.isdigit() else code)))).scalars().first()
        if p is not None:
            if tpl.kind == "pass":
                from ...services import seating
                ctx = await seating.pass_context(session, p)
                if ctx:
                    return ctx
            else:
                for it in await certs.certificates_for(session, p, [tpl]):
                    return it.context
    return certs.sample_context(tpl.kind)


_thumbs: "dict[tuple, bytes]" = {}


@router.get("/{tid}/preview.png")
async def preview(tid: int, code: str | None = None, w: int = 1400, user: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    w = max(200, min(1400, w))
    key = (tid, w, hash(tpl.design_json), st.get("olympiad_name")) if not code and w <= 600 else None
    if key and key in _thumbs:  # ro'yxatdagi kichik rasmlar — keshdan
        return Response(_thumbs[key], media_type="image/png", headers={"Cache-Control": "no-cache"})
    ctx = await _context_for(session, tpl, code)
    png = await asyncio.to_thread(lambda: dz.to_png(certs.render_template(tpl, ctx), w))
    if key:
        if len(_thumbs) > 100:
            _thumbs.clear()
        _thumbs[key] = png
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/{tid}/sample.pdf")
async def sample_pdf(tid: int, code: str | None = None, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    ctx = await _context_for(session, tpl, code)
    dpi = certs.template_dpi(tpl)
    pdf = await asyncio.to_thread(lambda: dz.to_pdf(certs.render_template(tpl, ctx), dpi))
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="namuna.pdf"'})


@router.post("/{tid}/delete")
async def delete(tid: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    tpl = await _get(session, tid)
    if tpl.kind == "pass":
        n = (await session.execute(select(func.count()).select_from(CertificateTemplate)
                                   .where(CertificateTemplate.kind == "pass"))).scalar_one()
        if n <= 1:
            flash(request, "Yagona ruxsatnoma shablonini o'chirib bo'lmaydi — uni o'zgartiring.", "error")
            return RedirectResponse(f"/admin/certificates/{tid}", 303)
    used = dz.assets_in(certs.load_design(tpl))
    if tpl.background_path:
        Path(tpl.background_path).unlink(missing_ok=True)
    await session.delete(tpl)
    audit.log(session, user, "delete", "certificate", tid, tpl.name, ip=client_ip(request))
    await session.commit()
    # Boshqa shablonlarda ishlatilmayotgan rasmlarni o'chiramiz
    others = set()
    for t in (await session.execute(select(CertificateTemplate))).scalars():
        others |= dz.assets_in(certs.load_design(t))
    for name in used - others:
        p = dz.asset_path(name)
        if p:
            p.unlink(missing_ok=True)
    flash(request, "Shablon o'chirildi")
    return RedirectResponse("/admin/certificates", 303)


# ---------------------------------------------------------------------------
# Ko'rib chiqish: kim qaysi sertifikatni oladi
# ---------------------------------------------------------------------------
async def _all_items(session: AsyncSession, tid: int | None = None, subject: str = "",
                     prize: str = "") -> list:
    """(ishtirokchi, sertifikat) juftliklari — filtrlar bilan."""
    templates = await certs.active_templates(session)
    if tid:
        templates = [t for t in templates if t.id == tid]
    people = (await session.execute(select(Participant).where(Participant.status == "active")
                                    .order_by(Participant.last_name, Participant.first_name))).scalars().all()
    out = []
    for p in people:
        for it in await certs.certificates_for(session, p, templates):
            if subject and it.title != subject:
                continue
            if prize == "yes" and it.prize is None or prize == "no" and it.prize is not None:
                continue
            if prize.isdigit() and it.prize != int(prize):
                continue
            out.append((p, it))
    return out


@router.get("/review")
async def review(request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    tid = int_or_none(params.get("tid"))
    items = await _all_items(session, tid, params.get("subject", ""), params.get("prize", ""))
    templates = await certs.active_templates(session)
    by_tpl = Counter(it.template.id for _, it in items)
    by_prize = Counter(it.prize for _, it in items)
    subjects = sorted({it.title for _, it in items})
    from ...services import ranking
    cur = await ranking.current_round(session)
    return render(request, "admin/certificate_review.html", {
        "items": items[:500], "total": len(items), "templates": templates, "by_tpl": by_tpl,
        "by_prize": by_prize, "subjects": subjects, "params": params, "round": cur,
        "rules": certs.RULES})


@router.post("/export")
async def export_start(request: Request, user: User = Depends(require_admin)):
    """Fonda tayyorlash: fmt=zip (har biri alohida PDF) yoki pdf (hammasi bitta PDF — chop etish uchun)."""
    form = await request.form()
    fmt = "pdf" if form.get("fmt") == "pdf" else "zip"
    tid = int_or_none(form.get("tid"))
    subject, prize = str(form.get("subject") or ""), str(form.get("prize") or "")
    who, ip = SimpleNamespace(id=user.id, username=user.username), client_ip(request)

    async def worker(job: jobs.Job) -> None:
        async with session_scope() as s:
            items = await _all_items(s, tid, subject, prize)
            job.total = len(items)
            if not items:
                job.error = "Mos sertifikat topilmadi (shablon faolmi, natijalar kiritilganmi?)"
                return
            stamp = secrets.token_hex(4)
            if fmt == "zip":
                path = jobs.exports_dir() / f"sertifikatlar_{stamp}.zip"
                with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
                    names: Counter = Counter()
                    for p, it in items:
                        dpi = certs.template_dpi(it.template)
                        pdf = await asyncio.to_thread(lambda it=it, dpi=dpi: dz.to_pdf(it.render(), dpi))
                        fname = it.filename
                        names[fname] += 1
                        if names[fname] > 1:
                            fname = fname.replace(".pdf", f"_{names[fname]}.pdf")
                        z.writestr(fname, pdf)
                        job.done += 1
                job.filename, job.media_type = "sertifikatlar.zip", "application/zip"
            else:
                path = jobs.exports_dir() / f"sertifikatlar_{stamp}.pdf"
                writer = JpegPdfWriter(path, certs.template_dpi(items[0][1].template), quality=88)
                try:
                    for p, it in items:
                        img = await asyncio.to_thread(it.render)
                        await asyncio.to_thread(writer.add, img)
                        job.done += 1
                finally:
                    writer.close()
                job.filename, job.media_type = "sertifikatlar.pdf", "application/pdf"
            job.path = path
            audit.log(s, who, "export", "certificate", tid or "", f"Sertifikatlar ({fmt}): {len(items)} ta", ip=ip)
            await s.commit()

    job = jobs.start("Sertifikatlar", user.id, worker)
    return HTMLResponse(_job_html(job))


def _job_html(job: jobs.Job) -> str:
    """HTMX: ish holati (tugamaguncha har 2 soniyada yangilanadi)."""
    from html import escape
    if job.status == "running":
        return (f'<div class="job" hx-get="/admin/certificates/jobs/{job.id}" hx-trigger="every 2s" hx-swap="outerHTML">'
                f'⏳ Tayyorlanmoqda: <b>{job.done}</b> / {job.total or "…"} '
                f'<div class="bar"><span style="width:{job.percent}%"></span></div></div>')
    if job.status == "done":
        return (f'<div class="job ok">✅ Tayyor: {job.done} ta. '
                f'<a class="btn sm primary" href="/admin/certificates/jobs/{job.id}/download">⬇️ Yuklab olish</a></div>')
    return f'<div class="job err">⚠️ {escape(job.error)}</div>'


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: User = Depends(require_admin)):
    job = jobs.get(job_id, user.id)
    if job is None:
        return HTMLResponse('<div class="job err">Ish topilmadi (server qayta ishga tushgan bo\'lishi mumkin)</div>')
    return HTMLResponse(_job_html(job))


@router.get("/jobs/{job_id}/download")
async def job_download(job_id: str, user: User = Depends(require_admin)):
    job = jobs.get(job_id, user.id)
    if job is None or job.status != "done" or not job.path or not job.path.exists():
        raise HTTPException(404, "Fayl topilmadi yoki muddati o'tgan")
    return FileResponse(job.path, media_type=job.media_type, filename=job.filename)

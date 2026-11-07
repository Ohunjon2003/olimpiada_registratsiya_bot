"""Voucherlar: sozlash, berish, botga yuborish, chop etish, tekshirish (QR) va «ishlatildi» deb belgilash.

/admin/vouchers      — admin sahifasi
/v/<raqam>           — OCHIQ tekshirish sahifasi (voucherdagi QR shu yerga olib keladi).
                       Admin tizimga kirgan bo'lsa — shu sahifada «✅ Ishlatildi» tugmasi chiqadi.
"""
from __future__ import annotations

import asyncio
import io
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Broadcast, Participant, Round, School, User, Voucher
from ...services import audit, ranking, vouchers
from ...services import broadcast as bsvc
from ...services import certificates as certs
from ...services import design as dz
from ...services import settings as st
from ...utils import fmt_dt, now_utc, search_norm
from ..core import (client_ip, csrf_token, flash, int_or_none, render, require_admin, verify_csrf)

router = APIRouter(dependencies=[Depends(verify_csrf)])
PAGE = 100
PRINT_PART = 150          # bitta PDF da ko'pi bilan (1 yadroli serverda ~20 soniya)


async def _round(session: AsyncSession, rid: Optional[int]) -> Round:
    if rid:
        r = await session.get(Round, rid)
        if r:
            return r
    cur = await ranking.current_round(session)
    if cur is None:
        raise HTTPException(400, "Tur yaratilmagan")
    return cur


def _back(rid: int, extra: str = "") -> RedirectResponse:
    return RedirectResponse(f"/admin/vouchers?round_id={rid}{extra}", 303)


def _guess_site(request: Request) -> str:
    """So'rovdan sayt manzili (nginx orqali https bo'lsa — https)."""
    base = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") == "https" and base.startswith("http://"):
        base = "https://" + base[7:]
    return base


# ---------------------------------------------------------------------------
# Admin sahifasi
# ---------------------------------------------------------------------------
@router.get("/admin/vouchers")
async def page(request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    round_ = await _round(session, int_or_none(params.get("round_id")))
    q = select(Voucher).join(Participant, Participant.id == Voucher.participant_id).where(Voucher.round_id == round_.id)
    status = params.get("status", "")
    if status == "active":
        q = q.where(Voucher.cancelled_at.is_(None), Voucher.redeemed_at.is_(None))
    elif status == "redeemed":
        q = q.where(Voucher.redeemed_at.is_not(None), Voucher.cancelled_at.is_(None))
    elif status == "cancelled":
        q = q.where(Voucher.cancelled_at.is_not(None))
    elif status == "unsent":
        q = q.where(Voucher.sent_at.is_(None), Voucher.cancelled_at.is_(None))
    if int_or_none(params.get("school_id")):
        q = q.where(Participant.school_id == int(params["school_id"]))
    needle = search_norm(params.get("q", ""))
    if needle:
        q = q.where(or_(Participant.search_text.contains(needle, autoescape=True),
                        Voucher.serial.ilike(f"%{params['q'].strip()}%")))
    pg = max(1, int_or_none(params.get("page")) or 1)
    total = len((await session.execute(q.with_only_columns(Voucher.id))).all())
    items = (await session.execute(q.order_by(Participant.last_name, Participant.first_name)
                                   .offset((pg - 1) * PAGE).limit(PAGE))).scalars().all()
    schools = (await session.execute(select(School).where(School.is_active == True)  # noqa: E712
                                     .order_by(School.position, School.name))).scalars().all()
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    tpl = await vouchers.template(session)
    return render(request, "admin/vouchers.html", {
        "round": round_, "rounds": rounds, "stats": await vouchers.stats(session, round_), "items": items,
        "total": total, "page": pg, "pages": max(1, -(-total // PAGE)), "params": params, "schools": schools,
        "tpl": tpl, "who": vouchers.WHO, "status_labels": vouchers.STATUS, "fmt": vouchers.fmt_amount,
        "site_guess": _guess_site(request), "site": vouchers.site_url(), "fmt_dt": fmt_dt,
        "valid_until": vouchers.valid_until(), "expired": vouchers.expired(), "print_part": PRINT_PART,
    })


@router.post("/admin/vouchers/settings")
async def save_settings(request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    form = await request.form()
    rid = int_or_none(form.get("round_id")) or 0
    raw_amount = "".join(ch for ch in str(form.get("amount") or "") if ch.isdigit())
    amount = int(raw_amount) if raw_amount else 0
    if not 0 < amount <= 1_000_000_000:
        flash(request, "Voucher qiymatini to'g'ri kiriting (masalan 2000000)", "error")
        return _back(rid)
    vu = str(form.get("valid_until") or "").strip()
    if vu:
        try:
            datetime.strptime(vu, "%Y-%m-%d")
        except ValueError:
            vu = ""
    site = str(form.get("site_url") or "").strip().rstrip("/")
    if site and not site.startswith(("http://", "https://")):
        site = "https://" + site
    who = str(form.get("who") or "attended")
    vals = {
        "voucher_amount": amount,
        "voucher_reason": str(form.get("reason") or "").strip()[:200],
        "voucher_terms": str(form.get("terms") or "").strip()[:600],
        "voucher_valid_until": vu,
        "voucher_who": who if who in vouchers.WHO else "attended",
        "voucher_prefix": "".join(ch for ch in str(form.get("prefix") or "V").upper() if ch.isalnum())[:6] or "V",
        "site_url": site[:200],
    }
    await st.set_many(session, vals)
    audit.log(session, user, "update", "settings", "vouchers",
              f"Voucher: {vouchers.fmt_amount(amount)} so'm, kimga: {vals['voucher_who']}", ip=client_ip(request))
    await session.commit()
    flash(request, "Voucher sozlamalari saqlandi. Berilgan voucherlar qiymati o'zgarmaydi — faqat yangilariga.")
    return _back(rid)


@router.post("/admin/vouchers/issue")
async def issue(request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    if not vouchers.site_url():
        await st.set_many(session, {"site_url": _guess_site(request)})   # QR havolasi uchun
    if await vouchers.template(session) is None:
        flash(request, "Faol voucher shabloni yo'q — «🎓 Sertifikatlar» → «🎟 Voucher» bo'limida yarating.", "error")
        return _back(round_.id)
    try:
        n = await vouchers.issue(session, round_, user.username)
    except ValueError as e:
        flash(request, str(e), "error")
        return _back(round_.id)
    audit.log(session, user, "create", "voucher", round_.id, f"Voucher berildi: {n} ta", ip=client_ip(request))
    await session.commit()
    if n:
        flash(request, f"✅ {n} ta o'quvchiga voucher berildi. Endi botga yuboring yoki chop eting.")
    else:
        flash(request, "Yangi voucher berilmadi: mos o'quvchilarning hammasida voucher bor "
                       "(yoki davomatda hali hech kim «Keldi» deb belgilanmagan).", "info")
    return _back(round_.id)


@router.post("/admin/vouchers/send")
async def send_all(request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    if await vouchers.template(session) is None:
        flash(request, "Faol voucher shabloni yo'q", "error")
        return _back(round_.id)
    only_unsent = form.get("mode") != "all"
    flt = {"voucher_round": str(round_.id), "voucher_unsent": "1" if only_unsent else ""}
    b = Broadcast(kind="vouchers", round_id=round_.id, filters_json=json.dumps(flt), scheduled_at=now_utc(),
                  created_by=user.username, text="Voucher: " + ("yuborilmaganlarga" if only_unsent else "hammaga (qayta)"))
    session.add(b)
    await session.flush()
    n = await bsvc.count_recipients(b)
    audit.log(session, user, "create", "broadcast", b.id, f"Voucherlar botga: {n} ta", ip=client_ip(request))
    await session.commit()
    bsvc.wake()
    flash(request, f"Yuborish boshlandi: {n} ta o'quvchi. Holati — «📣 Xabar yuborish» sahifasida.")
    return _back(round_.id)


async def _get(session: AsyncSession, vid: int) -> Voucher:
    v = await session.get(Voucher, vid)
    if v is None:
        raise HTTPException(404, "Voucher topilmadi")
    return v


@router.post("/admin/vouchers/{vid}/send")
async def send_one(vid: int, request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    from ...bot import loader
    v = await _get(session, vid)
    if loader.bot is None:
        flash(request, "Bot ishlamayapti (BOT_TOKEN yo'q)", "error")
        return _back(v.round_id)
    res = await vouchers.send(loader.bot, session, v.participant)
    await session.commit()
    flash(request, {"ok": "Voucher yuborildi", "blocked": "O'quvchi botni bloklagan",
                    "skip": "Voucher amalda emas yoki faol shablon yo'q"}.get(res, "Yuborib bo'lmadi"),
          "success" if res == "ok" else "error")
    return RedirectResponse(request.headers.get("referer") or f"/admin/vouchers?round_id={v.round_id}", 303)


@router.post("/admin/vouchers/{vid}/cancel")
async def cancel(vid: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    form = await request.form()
    v = await _get(session, vid)
    if v.redeemed_at:
        flash(request, "Ishlatilgan voucherni bekor qilib bo'lmaydi", "error")
        return _back(v.round_id)
    v.cancelled_at = now_utc()
    v.cancel_reason = str(form.get("reason") or "").strip()[:255]
    audit.log(session, user, "update", "voucher", v.id, f"Bekor qilindi: {v.serial} ({v.cancel_reason})",
              ip=client_ip(request))
    await session.commit()
    flash(request, f"Voucher {v.serial} bekor qilindi — QR tekshiruvda «Bekor qilingan» chiqadi.")
    return _back(v.round_id)


@router.post("/admin/vouchers/{vid}/restore")
async def restore(vid: int, request: Request, user: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    v = await _get(session, vid)
    v.cancelled_at, v.cancel_reason = None, ""
    audit.log(session, user, "update", "voucher", v.id, f"Qayta tiklandi: {v.serial}", ip=client_ip(request))
    await session.commit()
    flash(request, f"Voucher {v.serial} qayta amalda")
    return _back(v.round_id)


@router.post("/admin/vouchers/{vid}/unredeem")
async def unredeem(vid: int, request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    v = await _get(session, vid)
    old = f"{fmt_dt(v.redeemed_at)} {v.redeemed_by}"
    v.redeemed_at, v.redeemed_by, v.redeem_note = None, "", ""
    audit.log(session, user, "update", "voucher", v.id, f"«Ishlatildi» olib tashlandi: {v.serial} (avval: {old})",
              ip=client_ip(request))
    await session.commit()
    flash(request, f"Voucher {v.serial}: «ishlatildi» belgisi olib tashlandi")
    return RedirectResponse(request.headers.get("referer") or f"/admin/vouchers?round_id={v.round_id}", 303)


@router.get("/admin/vouchers/{vid}.{ext}")
async def file(vid: int, ext: str, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    v = await _get(session, vid)
    tpl = await vouchers.template(session)
    if tpl is None:
        raise HTTPException(404, "Faol voucher shabloni yo'q")
    img = await asyncio.to_thread(vouchers.render, tpl, v)
    name = f"voucher_{v.participant.code}"
    if ext == "pdf":
        data = await asyncio.to_thread(dz.to_pdf, img, certs.template_dpi(tpl))
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    if ext == "png":
        return Response(await asyncio.to_thread(dz.to_png, img, 1600), media_type="image/png")
    raise HTTPException(404)


def _two_per_a4(images: list, dpi: int) -> list:
    """A5 albom voucherlarni A4 varaqqa ikkitadan (qirqish chizig'i bilan) — chop etish arzon va tez."""
    from PIL import Image, ImageDraw
    if not images:
        return []
    w, h = images[0].size
    if w < h:                                # albom bo'lmasa — bittadan
        return images
    W, H = round(210 / 25.4 * dpi), round(297 / 25.4 * dpi)
    k = min(W / w, (H / 2) / h)
    tw, th = int(w * k), int(h * k)
    pages = []
    for i in range(0, len(images), 2):
        pg = Image.new("RGB", (W, H), "white")
        for j, im in enumerate(images[i:i + 2]):
            pg.paste(im.resize((tw, th), Image.LANCZOS) if (tw, th) != im.size else im,
                     ((W - tw) // 2, j * (H // 2) + (H // 2 - th) // 2))
        d = ImageDraw.Draw(pg)
        for x in range(0, W, 24):            # qirqish chizig'i (punktir)
            d.line([(x, H // 2), (x + 12, H // 2)], fill=(160, 160, 160), width=2)
        pages.append(pg)
    return pages


@router.get("/admin/vouchers-print.pdf")
async def print_pdf(request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    """Chop etish: maktab bo'yicha yoki qismlab (150 tadan), A4 varaqda 2 tadan."""
    params = dict(request.query_params)
    round_ = await _round(session, int_or_none(params.get("round_id")))
    tpl = await vouchers.template(session)
    if tpl is None:
        raise HTTPException(404, "Faol voucher shabloni yo'q")
    q = (select(Voucher).join(Participant, Participant.id == Voucher.participant_id)
         .where(Voucher.round_id == round_.id, Voucher.cancelled_at.is_(None), Voucher.redeemed_at.is_(None)))
    sid = int_or_none(params.get("school_id"))
    if sid:
        q = q.where(Participant.school_id == sid)
    part = max(1, int_or_none(params.get("part")) or 1)
    items = (await session.execute(q.order_by(Participant.school_id, Participant.last_name, Participant.first_name,
                                              Voucher.id)
                                   .offset((part - 1) * PRINT_PART).limit(PRINT_PART))).scalars().all()
    if not items:
        raise HTTPException(404, "Chop etish uchun voucher yo'q")
    dpi = certs.template_dpi(tpl)

    def build() -> bytes:
        imgs = [vouchers.render(tpl, v) for v in items]
        return dz.merge_pdf(_two_per_a4(imgs, dpi), dpi)
    data = await asyncio.to_thread(build)
    audit.log(session, user, "export", "voucher", round_.id, f"Voucher PDF: {len(items)} ta", ip=client_ip(request))
    await session.commit()
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="voucherlar_{sid or "hammasi"}_{part}.pdf"'})


@router.get("/admin/vouchers-export.xlsx")
async def export_xlsx(request: Request, user: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    round_ = await _round(session, int_or_none(request.query_params.get("round_id")))
    rows = (await session.execute(select(Voucher).join(Participant, Participant.id == Voucher.participant_id)
                                  .where(Voucher.round_id == round_.id)
                                  .order_by(Participant.school_id, Participant.last_name))).scalars().all()
    wb = Workbook()
    ws = wb.active
    ws.title = "Voucherlar"
    head = ["№", "Voucher raqami", "ID", "Familiya Ism", "Maktab", "Sinf", "Telefon", "Qiymati (so'm)", "Holati",
            "Berilgan", "Botga yuborilgan", "Ishlatilgan", "Kim belgiladi", "Izoh"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, v in enumerate(rows, 1):
        p = v.participant
        ws.append([i, v.serial, p.code, p.full_name, p.school.name if p.school else "", p.grade.name if p.grade else "",
                   p.phone, v.amount, vouchers.STATUS[v.status].split(" ", 1)[1], fmt_dt(v.issued_at), fmt_dt(v.sent_at),
                   fmt_dt(v.redeemed_at), v.redeemed_by, v.redeem_note or v.cancel_reason])
    for col, wdt in zip("ABCDEFGHIJKLMN", (5, 18, 7, 30, 22, 6, 15, 14, 14, 17, 17, 17, 14, 30)):
        ws.column_dimensions[col].width = wdt
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="voucherlar.xlsx"'})


# ---------------------------------------------------------------------------
# Ochiq tekshirish sahifasi (QR shu yerga olib keladi)
# ---------------------------------------------------------------------------
async def _maybe_admin(request: Request, session: AsyncSession) -> Optional[User]:
    """Tizimga kirgan admin bo'lsa — User, aks holda None (sahifa hammaga ochiq)."""
    from ..core import current_user
    try:
        u = await current_user(request, session)
    except Exception:
        return None
    return u if u.is_admin else None


@router.get("/v")
async def check_form(request: Request, serial: str = "", session: AsyncSession = Depends(get_session)):
    """Raqamni qo'lda kiritish (QR o'qilmasa)."""
    if serial.strip():
        v = await vouchers.find(session, serial)
        if v:
            return RedirectResponse(f"/v/{v.serial}", 303)
    admin = await _maybe_admin(request, session)
    return render(request, "voucher_check.html", {"v": None, "searched": serial.strip(), "admin": admin,
                                                  "olympiad": st.get("olympiad_name")})


@router.get("/v/{serial}")
async def check(serial: str, request: Request, session: AsyncSession = Depends(get_session)):
    v = await vouchers.find(session, serial)
    admin = await _maybe_admin(request, session)
    csrf_token(request)
    return render(request, "voucher_check.html", {
        "v": v, "searched": serial, "admin": admin, "olympiad": st.get("olympiad_name"),
        "fmt": vouchers.fmt_amount, "words": vouchers.amount_words, "fmt_dt": fmt_dt,
        "valid_until": vouchers.valid_until(), "expired": vouchers.expired(),
    }, status_code=200 if v else 404)


@router.post("/v/{serial}/redeem")
async def redeem(serial: str, request: Request, session: AsyncSession = Depends(get_session)):
    admin = await _maybe_admin(request, session)
    if admin is None:
        raise HTTPException(403, "Faqat admin «ishlatildi» deb belgilay oladi — avval tizimga kiring")
    v = await vouchers.find(session, serial)
    if v is None:
        raise HTTPException(404, "Voucher topilmadi")
    form = await request.form()
    if v.cancelled_at:
        flash(request, "Bu voucher bekor qilingan — ishlatib bo'lmaydi", "error")
    elif v.redeemed_at:
        flash(request, f"Bu voucher allaqachon ishlatilgan: {fmt_dt(v.redeemed_at)} ({v.redeemed_by})", "error")
    elif vouchers.expired():
        flash(request, "Voucher muddati o'tgan", "error")
    else:
        v.redeemed_at = now_utc()
        v.redeemed_by = admin.username
        v.redeem_note = str(form.get("note") or "").strip()[:255]
        audit.log(session, admin, "update", "voucher", v.id, f"Ishlatildi: {v.serial} · {v.amount} so'm · {v.redeem_note}",
                  ip=client_ip(request))
        await session.commit()
        flash(request, f"✅ Voucher {v.serial} «ishlatildi» deb belgilandi. Qayta ishlatib bo'lmaydi.")
    return RedirectResponse(f"/v/{v.serial}", 303)

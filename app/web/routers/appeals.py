"""Admin: natijaga e'tirozlar — ko'rish, javob berish, qabul qilishni yoqish/o'chirish."""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Appeal, Participant, Result, Subject, User
from ...services import audit
from ...services import settings as st
from ...utils import by_lang, now_utc, parse_local_input, search_norm
from ..core import (Page, client_ip, flash, int_or_none, render, require_admin,
                    verify_csrf)

router = APIRouter(prefix="/admin/appeals", dependencies=[Depends(verify_csrf)])

STATUS = {"new": "⏳ Yangi", "accepted": "✅ Qabul qilingan", "rejected": "❌ Rad etilgan"}


@router.get("")
async def appeals_page(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    status = params.get("status", "new")
    q = select(Appeal).join(Participant, Participant.id == Appeal.participant_id)
    if status in STATUS:
        q = q.where(Appeal.status == status)
    sid = int_or_none(params.get("subject_id"))
    if sid:
        q = q.where(Appeal.subject_id == sid)
    needle = search_norm(params.get("q", ""))
    if needle:
        q = q.where(Participant.search_text.contains(needle, autoescape=True))
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    page = Page(total, int_or_none(params.get("page")) or 1, 30, params)
    rows = (await session.execute(q.order_by(Appeal.id.desc()).offset(page.offset).limit(page.per_page))).scalars().all()

    # Har bir e'tiroz uchun hozirgi natija (ball)
    results = {}
    for a in rows:
        if a.subject_id and a.round_id:
            results[a.id] = (await session.execute(select(Result).where(
                Result.participant_id == a.participant_id, Result.subject_id == a.subject_id,
                Result.round_id == a.round_id))).scalars().first()
    counts = dict((await session.execute(select(Appeal.status, func.count()).group_by(Appeal.status))).all())
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    return render(request, "admin/appeals.html", {
        "rows": rows, "page": page, "params": params, "status": status, "STATUS": STATUS,
        "counts": counts, "subjects": subjects, "results": results,
    })


@router.post("/settings")
async def appeals_settings(request: Request, user: User = Depends(require_admin),
                           session: AsyncSession = Depends(get_session)):
    form = await request.form()
    try:
        until = parse_local_input(str(form.get("appeals_until") or ""))
    except ValueError:
        flash(request, "Sana noto'g'ri", "error")
        return RedirectResponse("/admin/appeals", 303)
    values = {"appeals_enabled": form.get("appeals_enabled") == "on", "appeals_until": until}
    await st.set_many(session, values)
    audit.log(session, user, "update", "settings", "", "E'tiroz sozlamalari", new=values, ip=client_ip(request))
    await session.commit()
    if values["appeals_enabled"] and not st.get("results_published"):
        flash(request, "Saqlandi. Diqqat: natijalar hali e'lon qilinmagan — «E'tiroz» tugmasi o'quvchilarga "
                       "natijalar e'lon qilingandan keyin chiqadi.", "warn")
    else:
        flash(request, "Saqlandi")
    return RedirectResponse("/admin/appeals", 303)


@router.post("/{aid}/answer")
async def appeal_answer(aid: int, request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    from ...bot import loader
    from ...bot.texts import t

    a = await session.get(Appeal, aid)
    if a is None:
        raise HTTPException(404)
    form = await request.form()
    decision = str(form.get("decision") or "")
    answer = str(form.get("answer") or "").replace("\r\n", "\n").strip()
    back = request.headers.get("referer") or "/admin/appeals"
    if decision not in ("accepted", "rejected"):
        raise HTTPException(400, "Noto'g'ri qaror")
    if not 2 <= len(answer) <= 1000:
        flash(request, f"№{aid}: javob matnini yozing (2–1000 belgi) — o'quvchiga shu matn yuboriladi.", "error")
        return RedirectResponse(back, 303)
    old = a.status
    a.status, a.answer, a.answered_by, a.answered_at = decision, answer, user.username, now_utc()
    p = a.participant
    subj = by_lang(a.subject.name, a.subject.name_cyr, p.lang) if a.subject else "—"
    audit.log(session, user, "update", "appeal", aid, f"e'tiroz №{aid}: {decision}",
              old={"status": old}, new={"status": decision, "answer": answer}, ip=client_ip(request))
    await session.commit()

    sent = False
    if loader.bot is not None:
        try:
            await loader.bot.send_message(p.telegram_id, t("appeal_answer", p.lang).format(
                id=aid, subject=escape(subj), status=t("appeal_" + decision, p.lang), answer=escape(answer)))
            sent = True
        except Exception:
            sent = False
    flash(request, f"№{aid}: javob saqlandi" + (" va o'quvchiga yuborildi" if sent
                                                else ". Botda yuborib bo'lmadi (bot ishlamayapti yoki o'quvchi bloklagan)"),
          "success" if sent else "warn")
    return RedirectResponse(back, 303)

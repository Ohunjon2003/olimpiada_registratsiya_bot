"""Admin: o'rinlar va mukofot qoidalari (har fan/blok uchun nechta sovrinli o'rin, minimal foiz)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import Block, Round, Subject, User
from ...services import audit, ranking
from ...services import certificates as certs
from ...services import settings as st
from ..core import client_ip, flash, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/awards", dependencies=[Depends(verify_csrf)])


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(v).strip())))
    except (TypeError, ValueError):
        return default


def _pct(v) -> float | None:
    s = str(v or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return max(0.0, min(100.0, float(s)))
    except ValueError:
        return None


@router.get("")
async def page(request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    subjects = (await session.execute(select(Subject).where(Subject.is_active == True)  # noqa: E712
                                      .order_by(Subject.position, Subject.id))).scalars().all()
    blocks = (await session.execute(select(Block).where(Block.is_active == True)  # noqa: E712
                                    .order_by(Block.position, Block.id))).scalars().all()
    cur = await ranking.current_round(session)
    # Joriy tur bo'yicha har fanda nechta sovrindor chiqishini ko'rsatamiz
    by_grade = st.get("ranking_by_grade")
    stats = {}
    if cur:
        for s in subjects:
            rows = await ranking.subject_ranking(session, s, cur, by_grade=by_grade)
            prizes = [certs.prize_place(r, f"s:{s.id}") for r in rows]
            stats[f"s:{s.id}"] = (len(rows), sum(1 for p in prizes if p == 1), sum(1 for p in prizes if p))
        for b in blocks:
            rows = await ranking.block_ranking(session, b, cur, by_grade=by_grade)
            prizes = [certs.prize_place(r, f"b:{b.id}") for r in rows]
            stats[f"b:{b.id}"] = (len(rows), sum(1 for p in prizes if p == 1), sum(1 for p in prizes if p))
    texts = {k: st.raw(k) or v for k, v in certs.AWARD_TEXT_DEFAULTS.items()}
    return render(request, "admin/awards.html", {
        "subjects": subjects, "blocks": blocks, "rules": certs.award_rules(), "round": cur,
        "stats": stats, "texts": texts, "defaults": certs.AWARD_TEXT_DEFAULTS})


@router.post("")
async def save(request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    form = await request.form()
    rules = {"default": {"places": _int(form.get("default_places"), 3, 0, 50),
                         "min_percent": _pct(form.get("default_min")) or 0.0}}
    keys = [k[len("places_"):] for k in form.keys() if k.startswith("places_")]
    for key in keys:
        if not (key.startswith("s:") or key.startswith("b:")) or not key[2:].isdigit():
            continue
        places, mn = str(form.get(f"places_{key}") or "").strip(), _pct(form.get(f"min_{key}"))
        if places == "" and mn is None:
            continue  # umumiy qoida ishlatiladi
        rules[key] = {"places": _int(places, rules["default"]["places"], 0, 50) if places else rules["default"]["places"],
                      "min_percent": mn if mn is not None else rules["default"]["min_percent"]}
    values = {"award_rules": json.dumps(rules, ensure_ascii=False),
              "ranking_by_grade": form.get("ranking_by_grade") == "1"}
    for k, default in certs.AWARD_TEXT_DEFAULTS.items():
        v = str(form.get(k) or "").strip()[:80]
        values[k] = "" if v == default else v
    await st.set_many(session, values)
    ranking.invalidate()
    audit.log(session, user, "update", "settings", "awards", "O'rin va mukofot qoidalari", new=rules, ip=client_ip(request))
    await session.commit()
    flash(request, "Mukofot qoidalari saqlandi")
    return RedirectResponse("/admin/awards", 303)


@router.post("/lock")
async def lock_round(request: Request, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    """Joriy turning natijalarini qulflash/ochish (qulflangach hakamlar ballni o'zgartira olmaydi)."""
    cur = await ranking.current_round(session)
    if cur is None:
        flash(request, "Tur yo'q", "error")
        return RedirectResponse("/admin/awards", 303)
    round_ = await session.get(Round, cur.id)
    round_.is_locked = not round_.is_locked
    audit.log(session, user, "update", "round", round_.id,
              f"{round_.name}: natijalar {'qulflandi' if round_.is_locked else 'ochildi'}", ip=client_ip(request))
    await session.commit()
    state = "qulflandi — o'rinlar yakuniy" if round_.is_locked else "ochildi"
    flash(request, f"«{round_.name}» natijalari {state}")
    return RedirectResponse("/admin/awards", 303)

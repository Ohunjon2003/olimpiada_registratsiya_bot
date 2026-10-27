"""Qisqa statistika: bot'dagi /stat buyrug'i uchun."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import (Appeal, Attendance, Grade, Participant, Result, Subject,
                      participant_subjects)
from ..utils import fmt_dt, now_utc, to_local
from . import backup, ranking
from . import settings as st


async def stat_text(session: AsyncSession) -> str:
    """Telegram uchun HTML ko'rinishdagi statistika."""
    ex = session.execute  # qisqartma
    total = (await ex(select(func.count()).select_from(Participant))).scalar_one()
    active = (await ex(select(func.count()).select_from(Participant)
                          .where(Participant.status == "active"))).scalar_one()
    day_start = to_local(now_utc()).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None)
    today = (await ex(select(func.count()).select_from(Participant)
                         .where(Participant.created_at >= day_start_utc))).scalar_one()

    reg = {"open": "🟢 ochiq", "closed": "🔴 yopilgan", "not_started": "🟡 hali boshlanmagan"}[st.registration_state()]
    lines = [f"📊 <b>{escape(st.get('olympiad_name'))}</b>",
             f"Ro'yxatdan o'tish: {reg}", "",
             f"👥 Jami: <b>{total}</b>  (bugun: +{today})",
             f"✅ Faol: {active}   ⛔️ Chetlatilgan: {total - active}"]

    by_subject = (await ex(
        select(Subject.id, Subject.name, func.count(participant_subjects.c.participant_id))
        .join(participant_subjects, participant_subjects.c.subject_id == Subject.id)
        .group_by(Subject.id).order_by(Subject.position, Subject.id))).all()
    if by_subject:
        lines += ["", "📚 <b>Fanlar bo'yicha</b>"] + [f"• {escape(n)}: {c}" for _, n, c in by_subject]

    by_grade = (await ex(
        select(Grade.name, func.count(Participant.id)).join(Participant, Participant.grade_id == Grade.id)
        .group_by(Grade.id).order_by(Grade.position, Grade.id))).all()
    if by_grade:
        lines += ["", "🎒 <b>Sinflar bo'yicha</b>", "  ".join(f"{escape(g)}: {c}" for g, c in by_grade)]

    cur = await ranking.current_round(session)
    if cur:
        att = dict((await ex(select(Attendance.present, func.count()).where(Attendance.round_id == cur.id)
                                .group_by(Attendance.present))).all())
        came, absent = att.get(True, 0), att.get(False, 0)
        lines += ["", f"🔁 <b>{escape(cur.name)}</b>",
                  f"🙋 Keldi: {came}   🚫 Kelmadi: {absent}   ❔ Belgilanmagan: {max(active - came - absent, 0)}"]
        entered = dict((await ex(select(Result.subject_id, func.count()).where(Result.round_id == cur.id)
                                    .group_by(Result.subject_id))).all())
        for sid, name, _ in by_subject:
            n = (await ex(select(func.count()).select_from(
                ranking.participants_in_round_query(cur, sid).subquery()))).scalar_one()
            lines.append(f"✍️ {escape(name)}: {entered.get(sid, 0)}/{n} natija")

    new_appeals = (await ex(select(func.count()).select_from(Appeal).where(Appeal.status == "new"))).scalar_one()
    if new_appeals:
        lines += ["", f"⚖️ Ko'rib chiqilmagan e'tirozlar: <b>{new_appeals}</b>"]

    last = backup.list_backups()
    if last:
        when = datetime.fromtimestamp(last[0].stat().st_mtime, config.TZ).strftime('%d.%m.%Y %H:%M')
        lines += ["", f"💾 Oxirgi zaxira: {when}"]
    lines += ["", f"🕒 {fmt_dt(now_utc())}"]
    return "\n".join(lines)

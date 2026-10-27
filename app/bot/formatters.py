"""Bot xabarlarini tayyorlash: ma'lumotlar xulosasi va natijalar matni.

Bu funksiyalardan web qism (ommaviy xabar yuborish) ham foydalanadi.
"""
from __future__ import annotations

from html import escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (CustomQuestion, Grade, Participant, Result, Round,
                      School, Subject)
from ..services import ranking
from ..services import settings as st
from ..utils import by_lang, fmt_num, format_phone
from .texts import t


def option_label(q: CustomQuestion, value: str, lang: str) -> str:
    """Saqlangan (lotin) variantni foydalanuvchi tiliga o'giradi."""
    if q.qtype not in ("choice", "multichoice"):
        return value
    if lang != "cyr":
        return value.replace("; ", ", ")
    lat = q.option_list()
    cyr = [o.strip() for o in (q.options_cyr or "").splitlines() if o.strip()]
    out = []
    for part in [v.strip() for v in value.split(";")] if q.qtype == "multichoice" else [value]:
        if part in lat:
            i = lat.index(part)
            out.append(by_lang(part, cyr[i] if i < len(cyr) else "", lang))
        else:
            out.append(part)
    return ", ".join(out)


async def summary_from_data(session: AsyncSession, data: dict, lang: str) -> str:
    """FSM ma'lumotlaridan tasdiqlash uchun xulosa."""
    school = await session.get(School, data.get("school_id") or 0)
    grade = await session.get(Grade, data.get("grade_id") or 0)
    subj_ids = data.get("subjects") or []
    subjects = (await session.execute(select(Subject).where(Subject.id.in_(subj_ids))
                                       .order_by(Subject.position, Subject.id))).scalars().all() if subj_ids else []
    lines = [
        f"👤 {t('f_last_name', lang)}: <b>{escape(data.get('last_name', ''))}</b>",
        f"👤 {t('f_first_name', lang)}: <b>{escape(data.get('first_name', ''))}</b>",
        f"📱 {t('f_phone', lang)}: <b>{format_phone(data.get('phone', ''))}</b>",
        f"🏫 {t('f_school', lang)}: <b>{escape(by_lang(school.name, school.name_cyr, lang)) if school else '—'}</b>",
        f"🏠 {t('f_address', lang)}: <b>{escape(data.get('address', ''))}</b>",
        f"🎓 {t('f_grade', lang)}: <b>{escape(by_lang(grade.name, grade.name_cyr, lang)) if grade else '—'}</b>",
        f"📚 {t('f_subjects', lang)}: <b>{escape(', '.join(by_lang(s.name, s.name_cyr, lang) for s in subjects)) or '—'}</b>",
    ]
    if data.get("instagram"):
        lines.append(f"📸 {t('f_instagram', lang)}: <b>@{escape(data['instagram'])}</b>")
    answers = data.get("answers") or {}
    if answers:
        qs = (await session.execute(select(CustomQuestion).where(
            CustomQuestion.id.in_([int(k) for k in answers]))
            .order_by(CustomQuestion.position, CustomQuestion.id))).scalars().all()
        for q in qs:
            val = answers.get(str(q.id), "")
            if val:
                lines.append(f"❓ {escape(by_lang(q.text, q.text_cyr, lang))}: "
                             f"<b>{escape(option_label(q, val, lang))}</b>")
    return "\n".join(lines)


def summary_from_participant(p: Participant, lang: str) -> str:
    lines = [
        f"🆔 {t('f_code', lang)}: <b>{p.code}</b>",
        f"👤 {t('f_last_name', lang)}: <b>{escape(p.last_name)}</b>",
        f"👤 {t('f_first_name', lang)}: <b>{escape(p.first_name)}</b>",
        f"📱 {t('f_phone', lang)}: <b>{format_phone(p.phone)}</b>",
        f"🏫 {t('f_school', lang)}: <b>{escape(by_lang(p.school.name, p.school.name_cyr, lang))}</b>",
        f"🏠 {t('f_address', lang)}: <b>{escape(p.address)}</b>",
        f"🎓 {t('f_grade', lang)}: <b>{escape(by_lang(p.grade.name, p.grade.name_cyr, lang))}</b>",
        f"📚 {t('f_subjects', lang)}: <b>{escape(', '.join(by_lang(s.name, s.name_cyr, lang) for s in p.subjects))}</b>",
    ]
    if p.instagram_username:
        lines.append(f"📸 {t('f_instagram', lang)}: <b>@{escape(p.instagram_username)}</b>")
    for a in sorted(p.answers, key=lambda a: (a.question.position, a.question.id) if a.question else (0, 0)):
        if a.question and a.value:
            lines.append(f"❓ {escape(by_lang(a.question.text, a.question.text_cyr, lang))}: "
                         f"<b>{escape(option_label(a.question, a.value, lang))}</b>")
    return "\n".join(lines)


def _scope(p: Participant, lang: str, by_grade: bool) -> str:
    if not by_grade:
        return t("scope_all", lang)
    g = by_lang(p.grade.name, p.grade.name_cyr, lang)
    # Sinf nomi "9-sinf" ko'rinishida kiritilgan bo'lsa takrorlanmasin
    if any(w in g.lower() for w in ("sinf", "синф")):
        return ("{g} орасида" if lang == "cyr" else "{g} orasida").format(g=g)
    return t("scope_grade", lang).format(grade=g)


async def results_text(session: AsyncSession, p: Participant) -> Optional[str]:
    """Ishtirokchining barcha turlardagi natijalari. Natija bo'lmasa None."""
    lang = p.lang
    by_grade = st.get("ranking_by_grade")
    rounds = (await session.execute(select(Round).where(Round.is_active == True)
                                    .order_by(Round.position, Round.id))).scalars().all()
    has_any = set((await session.execute(
        select(Result.round_id).where(Result.participant_id == p.id))).scalars().all())
    blocks_text = []
    for r in rounds:
        if r.id not in has_any:
            continue
        summ = await ranking.participant_summary(session, p, r, by_grade)
        parts = [t("results_header", lang).format(
            olympiad=escape(st.get("olympiad_name")), round=escape(by_lang(r.name, r.name_cyr, lang)),
            name=escape(p.full_name), code=p.code)]
        for item in summ["subjects"]:
            s, row = item["subject"], item["row"]
            name = escape(by_lang(s.name, s.name_cyr, lang))
            if row is None:
                parts.append(t("result_missing", lang).format(subject=name))
            else:
                parts.append(t("result_line", lang).format(
                    subject=name, score=fmt_num(row.score), max=fmt_num(row.max_score),
                    percent=fmt_num(row.percent), place=row.place,
                    scope=_scope(p, lang, by_grade), count=row.group_size))
        for item in summ["blocks"]:
            b, row = item["block"], item["row"]
            parts.append(t("block_line", lang).format(
                block=escape(by_lang(b.name, b.name_cyr, lang)), score=fmt_num(row.score),
                max=fmt_num(row.max_score), percent=fmt_num(row.percent), place=row.place,
                scope=_scope(p, lang, by_grade), count=row.group_size))
        blocks_text.append("\n\n".join(parts))
    if not blocks_text:
        return None
    return "\n\n➖➖➖➖➖\n\n".join(blocks_text)

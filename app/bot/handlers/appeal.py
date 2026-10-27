"""Natijaga e'tiroz (apellyatsiya): fan tanlash → matn yozish → saqlash va adminga xabar."""
from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from ...db import session_scope
from ...models import Appeal, Result, Subject
from ...services import notify, ranking
from ...services import participants as psvc
from ...utils import by_lang
from .. import keyboards as kb
from ..states import AppealSt
from ..texts import LAT, Btn, both, t
from .registration import can_edit_profile, safe_edit_markup

router = Router(name="appeal")

_STATUS_KEY = {"new": "appeal_pending", "accepted": "appeal_accepted", "rejected": "appeal_rejected"}


async def _back_to_menu(msg: Message, state: FSMContext, text: str, lang: str) -> None:
    """Holatni tozalab, xabar va asosiy menyuni ko'rsatadi."""
    await state.set_state(None)
    await state.set_data({"lang": lang})
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.chat.id)
        can_edit = p is not None and await can_edit_profile(s, p)
    await msg.answer(text, reply_markup=kb.menu_kb(lang, can_edit))


@router.message(StateFilter(None), Btn("btn_appeal"))
async def appeal_start(msg: Message, state: FSMContext) -> None:
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is None or p.status != "active":
            return  # ro'yxatdan o'tmagan — e'tiroz mumkin emas
        lang = p.lang
        if not kb.appeals_open():
            await msg.answer(t("appeal_closed", lang), reply_markup=kb.menu_kb(lang, await can_edit_profile(s, p)))
            return
        cur = await ranking.current_round(s)
        mine = (await s.execute(select(Appeal).where(Appeal.participant_id == p.id)
                                .order_by(Appeal.id))).scalars().all()
        results = (await s.execute(select(Result).where(Result.participant_id == p.id,
                                                        Result.round_id == (cur.id if cur else 0)))).scalars().all()
        subjects = {x.id: x for x in (await s.execute(select(Subject).where(
            Subject.id.in_([r.subject_id for r in results])).order_by(Subject.position, Subject.id))).scalars().all()}

    if mine:
        lines = [f"№{a.id} · {escape(by_lang(a.subject.name, a.subject.name_cyr, lang)) if a.subject else '—'}: "
                 f"{t(_STATUS_KEY.get(a.status, 'appeal_pending'), lang)}" for a in mine]
        await msg.answer(t("appeal_my", lang).format(list="\n".join(lines)))
    if not subjects:
        await msg.answer(t("appeal_none", lang))
        return
    rows = [[InlineKeyboardButton(text="📘 " + by_lang(sub.name, sub.name_cyr, lang), callback_data=f"apl:{sid}")]
            for sid, sub in subjects.items()]
    await msg.answer(t("appeal_choose", lang), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(StateFilter(None), F.data.startswith("apl:"))
async def appeal_subject(cb: CallbackQuery, state: FSMContext) -> None:
    sid = int(cb.data.split(":")[1]) if cb.data.split(":")[1].isdigit() else 0
    async with session_scope() as s:
        p = await psvc.by_telegram(s, cb.from_user.id)
        subject = await s.get(Subject, sid)
        if p is None or subject is None:
            await cb.answer()
            return
        lang = p.lang
        if not kb.appeals_open():
            await cb.answer(t("appeal_closed", lang), show_alert=True)
            return
        cur = await ranking.current_round(s)
        exists = (await s.execute(select(Appeal.id).where(
            Appeal.participant_id == p.id, Appeal.subject_id == sid,
            Appeal.round_id == cur.id if cur else Appeal.round_id.is_(None)))).first()
    await cb.answer()
    if exists:
        await cb.message.answer(t("appeal_exists", lang))
        return
    await safe_edit_markup(cb.message, None)
    await state.set_state(AppealSt.text)
    await state.update_data(lang=lang, appeal_subject=sid)
    await cb.message.answer(t("appeal_ask", lang).format(subject=escape(by_lang(subject.name, subject.name_cyr, lang))),
                            reply_markup=kb.nav_kb(lang, back=False))


def _is_any_button(text: str) -> bool:
    """Matn qaysidir tugmaga tengmi (menyu tugmasi bosilsa, uni e'tiroz deb saqlamaymiz)."""
    return any(text in both(k) for k in LAT if k.startswith("btn_"))


@router.message(AppealSt.text, F.text)
async def appeal_text(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    text = msg.text.strip()
    if _is_any_button(text):  # «Bekor qilish» yoki menyu tugmasi
        await _back_to_menu(msg, state, t("appeal_cancelled", lang), lang)
        return
    if not 10 <= len(text) <= 1000:
        await msg.answer(t("appeal_bad_len", lang))
        return
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        subject = await s.get(Subject, data.get("appeal_subject") or 0)
        if p is None or subject is None or not kb.appeals_open():
            await _back_to_menu(msg, state, t("appeal_closed", lang), lang)
            return
        cur = await ranking.current_round(s)
        a = Appeal(participant_id=p.id, subject_id=subject.id, round_id=cur.id if cur else None, text=text)
        s.add(a)
        await s.commit()
        aid, who, subj_name = a.id, f"{p.full_name} (ID {p.code})", subject.name
    await _back_to_menu(msg, state, t("appeal_saved", lang).format(id=aid), lang)
    await notify.send(f"⚖️ <b>Yangi e'tiroz №{aid}</b>\n👤 {escape(who)}\n📘 {escape(subj_name)}\n\n"
                      f"💬 {escape(text[:500])}\n\nPanel: «E'tirozlar» bo'limi")


@router.message(AppealSt.text)
async def appeal_not_text(msg: Message, state: FSMContext) -> None:
    """Rasm, stiker va h.k. — matn yozishni so'raymiz."""
    await msg.answer(t("appeal_bad_len", (await state.get_data()).get("lang", "lat")))

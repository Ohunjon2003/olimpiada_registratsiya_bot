"""/start, til tanlash va ro'yxatdan o'tganlar menyusi."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from ...db import session_scope
from ...services import certificates as certs
from ...services import design as dz
from ...services import participants as psvc
from ...services import settings as st
from .. import keyboards as kb
from ..formatters import results_text, summary_from_participant
from ..texts import Btn, t
from .registration import begin, can_edit_profile, safe_edit_markup, show_confirm, split_text

log = logging.getLogger("bot.menu")
router = Router(name="menu")        # /start va til — eng birinchi tekshiriladi
tail = Router(name="menu_tail")     # menyu tugmalari va qolgan xabarlar — eng oxirida


async def show_menu(msg: Message, state: FSMContext, text_key: str = "already") -> None:
    """Ro'yxatdan o'tgan foydalanuvchiga asosiy menyu."""
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.chat.id)
        if p is None:
            await begin(msg, state)
            return
        lang = p.lang
        if p.status != "active":
            await msg.answer(t("disqualified", lang), reply_markup=kb.remove_kb())
            return
        can_edit = await can_edit_profile(s, p)
    await msg.answer(t(text_key, lang), reply_markup=kb.menu_kb(lang, can_edit))


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is not None:
            if p.bot_blocked or (msg.from_user.username or "") != p.tg_username:
                p.bot_blocked = False
                p.tg_username = msg.from_user.username or ""
                await s.commit()
            await state.set_state(None)
            await state.set_data({"lang": p.lang})
    if p is not None:
        await show_menu(msg, state)
        return
    data = await state.get_data()
    if not data.get("lang"):
        await msg.answer(t("choose_lang"), reply_markup=kb.lang_kb())
        return
    await begin(msg, state)


@router.message(Command("lang"))
async def cmd_lang(msg: Message) -> None:
    await msg.answer(t("choose_lang"), reply_markup=kb.lang_kb())


@router.callback_query(F.data.startswith("lang:"))
async def lang_chosen(cb: CallbackQuery, state: FSMContext) -> None:
    lang = "cyr" if cb.data == "lang:cyr" else "lat"
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    async with session_scope() as s:
        p = await psvc.by_telegram(s, cb.from_user.id)
        if p is not None:
            p.lang = lang
            await s.commit()
    if p is not None:
        cur = await state.get_state()
        await state.update_data(lang=lang)
        if cur is None:
            await show_menu(cb.message, state, "lang_changed")
        else:
            # Ma'lumotni tahrirlash jarayonida til o'zgardi — tasdiqlash oynasiga qaytamiz
            await show_confirm(cb.message, state)
        return
    await state.update_data(lang=lang)
    await begin(cb.message, state)


# ---------------------------------------------------------------------------
# Menyu tugmalari (faqat holat yo'q paytda)
# ---------------------------------------------------------------------------
@tail.message(StateFilter(None), Btn("btn_profile"))
async def my_profile(msg: Message, state: FSMContext) -> None:
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is None:
            await cmd_start(msg, state)
            return
        await msg.answer(t("profile", p.lang).format(summary=summary_from_participant(p, p.lang)))


@tail.message(StateFilter(None), Btn("btn_results"))
async def my_results(msg: Message, state: FSMContext) -> None:
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is None:
            await cmd_start(msg, state)
            return
        lang = p.lang
        if p.status != "active":
            await msg.answer(t("disqualified", lang))
            return
        if not st.get("results_published"):
            await msg.answer(t("results_not_published", lang))
            return
        text = await results_text(s, p)
    if not text:
        await msg.answer(t("results_none", lang))
        return
    for part in split_text(text):
        await msg.answer(part)


@tail.message(StateFilter(None), Btn("btn_cert"))
async def my_certificates(msg: Message, state: FSMContext) -> None:
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is None:
            await cmd_start(msg, state)
            return
        lang = p.lang
        if p.status != "active":
            await msg.answer(t("disqualified", lang))
            return
        if not (st.get("certificates_enabled") and st.get("results_published")):
            await msg.answer(t("cert_not_ready", lang))
            return
        items = await certs.certificates_for(s, p)
    if not items:
        await msg.answer(t("cert_none", lang))
        return
    wait = await msg.answer(t("cert_preparing", lang))
    for item in items[:10]:
        # Rasm chizish CPU ishi — botni to'xtatmaslik uchun alohida oqimda
        img = await asyncio.to_thread(item.render)
        jpg = await asyncio.to_thread(dz.to_jpeg, img)
        pdf = await asyncio.to_thread(dz.to_pdf, img, certs.template_dpi(item.template))
        await msg.answer_photo(BufferedInputFile(jpg, filename=item.filename.replace(".pdf", ".jpg")),
                               caption=certs.caption_for(item, lang))
        await msg.answer_document(BufferedInputFile(pdf, filename=item.filename))
    try:
        await wait.delete()
    except Exception:
        pass


@tail.message(StateFilter(None), Btn("btn_pass"))
async def my_pass(msg: Message, state: FSMContext) -> None:
    """Ruxsatnoma: olimpiada kuni qayerda va qachon."""
    from ...services import seating
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is None:
            await cmd_start(msg, state)
            return
        lang = p.lang
        if p.status != "active":
            await msg.answer(t("disqualified", lang))
            return
        if not st.get("passes_published"):
            await msg.answer(t("pass_not_ready", lang))
            return
        if await seating.current_assignment(s, p) is None:
            await msg.answer(t("pass_none", lang))
            return
        result = await seating.send_pass(msg.bot, s, p)
        await s.commit()
    if result not in ("ok", "blocked"):
        await msg.answer(t("error", lang))


@tail.message(StateFilter(None), Btn("btn_lang"))
async def change_lang(msg: Message) -> None:
    await msg.answer(t("choose_lang"), reply_markup=kb.lang_kb())


@tail.message(StateFilter(None), Btn("btn_edit_profile"))
async def edit_profile(msg: Message, state: FSMContext) -> None:
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
        if p is None:
            await cmd_start(msg, state)
            return
        lang = p.lang
        if not await can_edit_profile(s, p):
            await msg.answer(t("edit_closed", lang), reply_markup=kb.menu_kb(lang, False))
            return
        data = {
            "lang": lang, "mode": "update",
            "first_name": p.first_name, "last_name": p.last_name, "phone": p.phone,
            "school_id": p.school_id, "address": p.address, "grade_id": p.grade_id,
            "subjects": [x.id for x in p.subjects], "instagram": p.instagram_username,
            "answers": {str(a.question_id): a.value for a in p.answers},
            "rules_ok": True,
        }
    await state.set_data(data)
    await msg.answer("✏️", reply_markup=kb.nav_kb(lang))
    await show_confirm(msg, state)


@tail.message(StateFilter(None))
async def fallback(msg: Message, state: FSMContext) -> None:
    """Holatsiz boshqa xabarlar: ro'yxatdan o'tgan bo'lsa menyu, aks holda /start."""
    async with session_scope() as s:
        p = await psvc.by_telegram(s, msg.from_user.id)
    if p is None:
        await cmd_start(msg, state)
        return
    await show_menu(msg, state, "use_menu")


@tail.callback_query()
async def stale_callback(cb: CallbackQuery) -> None:
    """Eskirgan tugmalar bosilganda 'soat' belgisini o'chiramiz."""
    await cb.answer()

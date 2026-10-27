"""Ro'yxatdan o'tish oqimi.

Oqim: obuna → (instagram) → nizom → ism → familiya → telefon → maktab →
manzil → sinf → fanlar → qo'shimcha savollar → tasdiqlash → saqlash.

Har bosqichda javob tekshiriladi; noto'g'ri bo'lsa ogohlantirib, shu savol
qayta so'raladi va keyingi bosqichga o'tkazilmaydi.
"""
from __future__ import annotations

import logging
from datetime import datetime
import re
from html import escape
from pathlib import Path
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ...db import session_scope
from ...models import (Channel, CustomAnswer, CustomQuestion, Grade,
                       Participant, Result, Rule, School, Subject)
from ...services import participants as psvc
from ...services import ranking
from ...services import settings as st
from ...utils import by_lang, clean_name, fmt_dt, fmt_num, format_phone, normalize_phone, search_norm
from .. import keyboards as kb
from ..formatters import summary_from_data
from ..states import STEPS, Reg
from .. import stickers
from ..texts import Btn, t

log = logging.getLogger("bot.reg")
router = Router(name="registration")

REG_STATES = [s for s in Reg.__all_states__]
INSTAGRAM_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
MAX_MSG = 4000


# ---------------------------------------------------------------------------
# Yordamchilar
# ---------------------------------------------------------------------------
async def get_lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang") or "lat"


async def safe_edit_markup(msg: Message, markup=None) -> None:
    """Tugmalarni almashtirish (o'zgarmagan bo'lsa Telegram xato beradi — e'tiborsiz qoldiramiz)."""
    try:
        await msg.edit_reply_markup(reply_markup=markup)
    except TelegramBadRequest:
        pass


def split_text(text: str, size: int = MAX_MSG) -> list[str]:
    """Uzun matnni Telegram chegarasiga (4096) mos bo'laklarga ajratadi."""
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > size:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) + 1 > size:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return parts or [""]


async def active_channels(session) -> list[Channel]:
    return list((await session.execute(select(Channel).where(Channel.is_active == True)
                                       .order_by(Channel.position, Channel.id))).scalars().all())


# Kanalni tekshirib bo'lmagan holatlar (bot admin emas, chat_id xato va h.k.): chat_id → (vaqt, xato).
# Bosh sahifada admin uchun ogohlantirish sifatida ko'rsatiladi.
channel_errors: dict[str, tuple[datetime, str]] = {}


async def is_member(bot: Bot, chat_id: str, user_id: int) -> Optional[bool]:
    """Foydalanuvchi Telegram kanal a'zosimi. Bot kanalda admin bo'lishi shart.

    None — tekshirib bo'lmadi (sozlash xatosi yoki Telegram javob bermadi).
    """
    try:
        m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as e:  # kanal topilmadi, bot admin emas, tarmoq xatosi va h.k.
        log.warning("get_chat_member(%s) xato: %s", chat_id, e)
        channel_errors[chat_id] = (datetime.now(), str(e)[:200])
        from ...services import notify
        await notify.error(f"Bot {chat_id} kanaliga obunani tekshira olmadi",
                           f"{e}\n\nBotni kanalga admin qiling. Shu vaqtgacha bu kanal tekshirilmaydi.",
                           key=f"channel:{chat_id}", every=3600)
        return None
    channel_errors.pop(chat_id, None)
    return m.status in ("member", "administrator", "creator") or \
        (m.status == "restricted" and getattr(m, "is_member", False))


async def missing_channels(bot: Bot, user_id: int, channels: list[Channel]) -> list[Channel]:
    """Obuna bo'linmagan (tekshiriladigan) Telegram kanallar.

    Kanalni tekshirib bo'lmasa (bot admin qilinmagan va h.k.) o'quvchi to'xtatilmaydi:
    aks holda bitta sozlash xatosi butun ro'yxatdan o'tishni to'xtatib qo'yadi.
    Admin bu haqda bosh sahifada ogohlantiriladi.
    """
    out = []
    for c in channels:
        if c.kind == "telegram" and c.verify and c.chat_id.strip():
            if await is_member(bot, c.chat_id.strip(), user_id) is False:
                out.append(c)
    return out


def instagram_required(channels: list[Channel]) -> bool:
    return bool(st.get("instagram_username_required")) and any(c.kind == "instagram" for c in channels)


async def subjects_for_grade(session, grade_id: int) -> list[Subject]:
    subs = (await session.execute(select(Subject).where(Subject.is_active == True)
                                  .order_by(Subject.position, Subject.id))).scalars().all()
    return [s for s in subs if not s.grades or any(g.id == grade_id for g in s.grades)]


async def active_questions(session) -> list[CustomQuestion]:
    return list((await session.execute(select(CustomQuestion).where(CustomQuestion.is_active == True)
                                       .order_by(CustomQuestion.position, CustomQuestion.id))).scalars().all())


def q_options(q: CustomQuestion, lang: str) -> list[str]:
    lat = q.option_list()
    cyr = [o.strip() for o in (q.options_cyr or "").splitlines() if o.strip()]
    return [by_lang(o, cyr[i] if i < len(cyr) else "", lang) for i, o in enumerate(lat)]


async def setup_ready(session) -> bool:
    """Maktab, sinf va fan kiritilganmi."""
    for model in (School, Grade, Subject):
        n = (await session.execute(select(func.count()).select_from(model)
                                   .where(model.is_active == True))).scalar_one()
        if n == 0:
            return False
    return True


async def can_edit_profile(session, p: Participant) -> bool:
    if not st.get("allow_profile_edit") or st.registration_state() != "open" or p.status != "active":
        return False
    n = (await session.execute(select(func.count()).select_from(Result)
                               .where(Result.participant_id == p.id))).scalar_one()
    return n == 0


# ---------------------------------------------------------------------------
# Orqaga / Bekor qilish
# ---------------------------------------------------------------------------
@router.message(StateFilter(*REG_STATES), Btn("btn_cancel"))
async def cancel(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    await state.set_state(None)
    await state.set_data({"lang": lang})
    if data.get("mode") == "update":
        from .menu import show_menu
        await msg.answer(t("edit_cancelled", lang))
        await show_menu(msg, state)
        return
    await msg.answer(t("cancelled", lang), reply_markup=kb.remove_kb())


@router.message(StateFilter(*REG_STATES), Btn("btn_back"))
async def back(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cur = (await state.get_state() or "").split(":")[-1]
    if data.get("editing") and cur not in ("confirm", "edit_menu"):
        await show_confirm(msg, state)
        return
    if cur in ("subscribe", "rules", "instagram", "first_name"):
        # Birinchi bosqichdan orqaga yo'q — shu savolni qayta beramiz
        if cur == "first_name":
            await ask(msg, state, "first_name")
        elif cur == "rules":
            await show_rules(msg, state)
        elif cur == "subscribe":
            await show_subscribe(msg, state)
        else:
            await msg.answer(t("ask_instagram", await get_lang(state)))
        return
    if cur == "custom":
        idx = int(data.get("q_index", 0))
        if idx > 0:
            await ask_custom(msg, state, idx - 1)
        else:
            await ask(msg, state, "subjects")
        return
    if cur in ("confirm", "edit_menu"):
        if data.get("mode") == "update" or data.get("editing"):
            await show_confirm(msg, state)
            return
        q_ids = data.get("q_ids") or []
        if q_ids:
            await ask_custom(msg, state, len(q_ids) - 1)
        else:
            await ask(msg, state, "subjects")
        return
    if cur in STEPS:
        await ask(msg, state, STEPS[max(0, STEPS.index(cur) - 1)])


# ---------------------------------------------------------------------------
# Boshlash: obuna → instagram → nizom
# ---------------------------------------------------------------------------
async def begin(msg: Message, state: FSMContext) -> None:
    """Yangi ro'yxatdan o'tishni boshlaydi (til tanlangandan keyin)."""
    lang = await get_lang(state)
    await state.set_state(None)
    await state.set_data({"lang": lang, "mode": "new"})

    rs = st.registration_state()
    if rs == "not_started":
        await msg.answer(t("reg_not_started", lang).format(start=fmt_dt(st.get("registration_start"))),
                         reply_markup=kb.remove_kb())
        return
    if rs == "closed":
        await msg.answer(t("reg_closed", lang), reply_markup=kb.remove_kb())
        return
    async with session_scope() as s:
        if not await setup_ready(s):
            await msg.answer(t("no_setup", lang), reply_markup=kb.remove_kb())
            return

    await stickers.send(msg.bot, msg.chat.id, "sticker_welcome")
    welcome = by_lang(st.get("welcome_text"), st.get("welcome_text_cyr"), lang)
    welcome = welcome.replace("{olympiad}", by_lang(st.get("olympiad_name"), "", lang))
    if welcome.strip():
        await msg.answer(welcome, parse_mode=None, reply_markup=kb.remove_kb())
    await show_subscribe(msg, state)


async def show_subscribe(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    async with session_scope() as s:
        channels = await active_channels(s)
    if not channels:
        await after_subscribe(msg, state)
        return
    await state.set_state(Reg.subscribe)
    await msg.answer(t("subscribe", lang), reply_markup=kb.channels_kb(channels, lang),
                     disable_web_page_preview=True)


@router.callback_query(Reg.subscribe, F.data == "sub:check")
async def sub_check(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    lang = await get_lang(state)
    async with session_scope() as s:
        channels = await active_channels(s)
    missing = await missing_channels(bot, cb.from_user.id, channels)
    if missing:
        names = "\n".join("• " + by_lang(c.title, c.title_cyr, lang) for c in missing)
        alert = t("not_subscribed", lang).format(list=names)
        # Alert 200 belgidan oshmasligi kerak
        await cb.answer(alert[:197] + "..." if len(alert) > 200 else alert, show_alert=True)
        return
    await cb.answer(t("subscribed_ok", lang))
    await safe_edit_markup(cb.message, None)
    await after_subscribe(cb.message, state)


async def after_subscribe(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    async with session_scope() as s:
        channels = await active_channels(s)
    if instagram_required(channels) and not data.get("instagram"):
        await state.set_state(Reg.instagram)
        await msg.answer(t("ask_instagram", lang), reply_markup=kb.nav_kb(lang, back=False))
        return
    if data.get("resub"):
        # Saqlash oldidan qayta obuna so'ralgan edi — tasdiqlashga qaytamiz
        await state.update_data(resub=False)
        await show_confirm(msg, state)
        return
    await show_rules(msg, state)


@router.message(Reg.instagram, F.text, ~Btn("btn_back", "btn_cancel"))
async def got_instagram(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    username = msg.text.strip().lstrip("@")
    # instagram.com/username havolasi yuborilgan bo'lsa ham qabul qilamiz
    m = re.search(r"instagram\.com/([A-Za-z0-9._]+)", username)
    if m:
        username = m.group(1)
    if not INSTAGRAM_RE.match(username):
        await msg.answer(t("bad_instagram", lang))
        return
    await state.update_data(instagram=username.lower())
    data = await state.get_data()
    if data.get("editing"):
        await show_confirm(msg, state)
    elif data.get("resub"):
        await state.update_data(resub=False)
        await show_confirm(msg, state)
    else:
        await show_rules(msg, state)


async def show_rules(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    async with session_scope() as s:
        rules = (await s.execute(select(Rule).where(Rule.is_active == True)
                                 .order_by(Rule.position, Rule.id))).scalars().all()
        if not rules:
            await ask(msg, state, "first_name")
            return
        await state.set_state(Reg.rules)
        await msg.answer(t("rules_intro", lang), reply_markup=kb.remove_kb())
        for r in rules:
            title = by_lang(r.title, r.title_cyr, lang)
            text = by_lang(r.text, r.text_cyr, lang).strip()
            if text:
                for i, part in enumerate(split_text(f"📌 {title}\n\n{text}")):
                    await msg.answer(part, parse_mode=None, disable_web_page_preview=True)
            if r.file_path and Path(r.file_path).exists():
                caption = f"📎 {title}"[:1000]
                try:
                    if r.tg_file_id:
                        await msg.answer_document(r.tg_file_id, caption=caption, parse_mode=None)
                    else:
                        sent = await msg.answer_document(
                            FSInputFile(r.file_path, filename=r.file_name or Path(r.file_path).name),
                            caption=caption, parse_mode=None)
                        if sent.document:
                            r.tg_file_id = sent.document.file_id  # keyingi safar qayta yuklamaslik uchun
                            await s.commit()
                except TelegramBadRequest as e:
                    log.warning("Nizom faylini yuborib bo'lmadi: %s", e)
                    r.tg_file_id = ""
                    await s.commit()
    await msg.answer(t("rules_confirm", lang), reply_markup=kb.rules_ok_kb(lang))


@router.callback_query(Reg.rules, F.data == "rules:ok")
async def rules_ok(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    await state.update_data(rules_ok=True)
    await ask(cb.message, state, "first_name")


@router.message(Reg.rules)
async def rules_text(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    await msg.answer(t("rules_confirm", lang), reply_markup=kb.rules_ok_kb(lang))


@router.message(Reg.subscribe)
async def subscribe_text(msg: Message, state: FSMContext) -> None:
    await show_subscribe(msg, state)


# ---------------------------------------------------------------------------
# Savol berish (har bosqich uchun)
# ---------------------------------------------------------------------------
async def ask(msg: Message, state: FSMContext, step: str) -> None:
    lang = await get_lang(state)
    if step == "first_name":
        await state.set_state(Reg.first_name)
        await msg.answer(t("ask_first_name", lang).format(example=escape(t("ex_first_name", lang))),
                         reply_markup=kb.nav_kb(lang, back=False))
    elif step == "last_name":
        await state.set_state(Reg.last_name)
        await msg.answer(t("ask_last_name", lang).format(example=escape(t("ex_last_name", lang))),
                         reply_markup=kb.nav_kb(lang))
    elif step == "phone":
        await state.set_state(Reg.phone)
        await msg.answer(t("ask_phone", lang), reply_markup=kb.phone_kb(lang))
    elif step == "school":
        await state.set_state(Reg.school)
        await send_schools(msg, state, page=0)
    elif step == "address":
        await state.set_state(Reg.address)
        await msg.answer(t("ask_address", lang), reply_markup=kb.nav_kb(lang))
    elif step == "grade":
        await state.set_state(Reg.grade)
        async with session_scope() as s:
            grades = (await s.execute(select(Grade).where(Grade.is_active == True)
                                      .order_by(Grade.position, Grade.id))).scalars().all()
        await msg.answer(t("ask_grade", lang), reply_markup=kb.grades_kb(grades, lang))
    elif step == "subjects":
        await send_subjects(msg, state)
    elif step == "custom":
        async with session_scope() as s:
            qs = await active_questions(s)
        await state.update_data(q_ids=[q.id for q in qs])
        if not qs:
            await show_confirm(msg, state)
        else:
            await ask_custom(msg, state, 0)
    elif step == "confirm":
        await show_confirm(msg, state)


async def advance(msg: Message, state: FSMContext, current: str) -> None:
    """Javob qabul qilingach keyingi bosqichga o'tadi."""
    data = await state.get_data()
    if data.get("editing"):
        if current == "grade":
            # Sinf o'zgarsa, tanlangan fanlar yangi sinfga mos kelishi shart
            async with session_scope() as s:
                allowed = {x.id for x in await subjects_for_grade(s, data["grade_id"])}
            chosen = set(data.get("subjects") or [])
            if not chosen or not chosen <= allowed or data.get("subjects_dropped"):
                await state.update_data(subjects_dropped=False)
                await state.update_data(subjects=[x for x in chosen if x in allowed])
                await msg.answer(t("subjects_reset", await get_lang(state)))
                await send_subjects(msg, state)
                return
        await show_confirm(msg, state)
        return
    await ask(msg, state, STEPS[STEPS.index(current) + 1])


# ---------------------------------------------------------------------------
# Ism, familiya
# ---------------------------------------------------------------------------
@router.message(Reg.first_name, F.text)
async def got_first_name(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    name = clean_name(msg.text)
    if not name:
        await msg.answer(t("bad_name", lang).format(example=escape(t("ex_first_name", lang))))
        return
    await state.update_data(first_name=name)
    await advance(msg, state, "first_name")


@router.message(Reg.last_name, F.text)
async def got_last_name(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    name = clean_name(msg.text)
    if not name:
        await msg.answer(t("bad_name", lang).format(example=escape(t("ex_last_name", lang))))
        return
    await state.update_data(last_name=name)
    await advance(msg, state, "last_name")


# ---------------------------------------------------------------------------
# Telefon (kontakt yoki qo'lda)
# ---------------------------------------------------------------------------
async def _accept_phone(msg: Message, state: FSMContext, phone: str) -> None:
    lang = await get_lang(state)
    async with session_scope() as s:
        if await psvc.phone_taken(s, phone, exclude_telegram_id=msg.from_user.id):
            await msg.answer(t("phone_taken", lang), reply_markup=kb.phone_kb(lang))
            return
    await state.update_data(phone=phone)
    # Kontakt tugmasini olib, oddiy navigatsiya tugmalarini qaytaramiz
    await msg.answer(f"✅ {format_phone(phone)}", reply_markup=kb.nav_kb(lang))
    await advance(msg, state, "phone")


@router.message(Reg.phone, F.contact)
async def got_contact(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    if msg.contact.user_id and msg.contact.user_id != msg.from_user.id:
        await msg.answer(t("foreign_contact", lang), reply_markup=kb.phone_kb(lang))
        return
    phone = normalize_phone(msg.contact.phone_number)
    if not phone:
        await msg.answer(t("bad_phone", lang), reply_markup=kb.phone_kb(lang))
        return
    await _accept_phone(msg, state, phone)


@router.message(Reg.phone, F.text)
async def got_phone_text(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    phone = normalize_phone(msg.text)
    if not phone:
        await msg.answer(t("bad_phone", lang), reply_markup=kb.phone_kb(lang))
        return
    await _accept_phone(msg, state, phone)


# ---------------------------------------------------------------------------
# Maktab (ro'yxatdan tanlash, sahifalash, qidirish)
# ---------------------------------------------------------------------------
async def _schools(session) -> list[School]:
    return list((await session.execute(select(School).where(School.is_active == True)
                                       .order_by(School.position, School.name))).scalars().all())


async def send_schools(msg: Message, state: FSMContext, page: int, edit: bool = False) -> None:
    lang = await get_lang(state)
    async with session_scope() as s:
        schools = await _schools(s)
    pages = max(1, (len(schools) + kb.SCHOOLS_PER_PAGE - 1) // kb.SCHOOLS_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = schools[page * kb.SCHOOLS_PER_PAGE:(page + 1) * kb.SCHOOLS_PER_PAGE]
    markup = kb.schools_kb(chunk, lang, page, len(schools))
    if edit:
        await safe_edit_markup(msg, markup)
    else:
        await msg.answer(t("ask_school", lang), reply_markup=markup)


@router.callback_query(Reg.school, F.data.startswith("schp:"))
async def school_page(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    page = int(cb.data.split(":")[1])
    # Qidiruv natijasidan "butun ro'yxat"ga qaytishda xabar matnini ham almashtiramiz
    if cb.message.text and cb.message.text != t("ask_school", await get_lang(state)):
        try:
            await cb.message.edit_text(t("ask_school", await get_lang(state)))
        except TelegramBadRequest:
            pass
    await send_schools(cb.message, state, page, edit=True)


@router.callback_query(Reg.school, F.data.startswith("sch:"))
async def school_chosen(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(state)
    sid = int(cb.data.split(":")[1])
    async with session_scope() as s:
        school = await s.get(School, sid)
    if not school or not school.is_active:
        await cb.answer(t("choose_from_list", lang), show_alert=True)
        return
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    await cb.message.answer(f"🏫 <b>{escape(by_lang(school.name, school.name_cyr, lang))}</b>")
    await state.update_data(school_id=sid)
    await advance(cb.message, state, "school")


@router.message(Reg.school, F.text)
async def school_search(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    needle = search_norm(msg.text)
    async with session_scope() as s:
        schools = await _schools(s)
    found = [x for x in schools if needle and (needle in search_norm(x.name) or
                                               needle in search_norm(x.name_cyr or ""))]
    # "5" deb yozilsa "15-maktab" ham chiqmasligi uchun: raqam bo'lsa avval aniq mosini ko'rsatamiz
    if needle.isdigit():
        exact = [x for x in found if re.search(rf"(?<!\d){needle}(?!\d)", search_norm(x.name))]
        found = exact or found
    if not found:
        await msg.answer(t("school_not_found", lang).format(q=escape(msg.text[:50])))
        await send_schools(msg, state, page=0)
        return
    await msg.answer(t("school_found", lang),
                     reply_markup=kb.schools_kb(found[:20], lang, 0, len(found[:20]), searching=True))


# ---------------------------------------------------------------------------
# Manzil
# ---------------------------------------------------------------------------
@router.message(Reg.address, F.text)
async def got_address(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    addr = re.sub(r"\s+", " ", msg.text).strip()
    if not (5 <= len(addr) <= 200) or sum(c.isalpha() for c in addr) < 3:
        await msg.answer(t("bad_address", lang))
        return
    await state.update_data(address=addr)
    await advance(msg, state, "address")


# ---------------------------------------------------------------------------
# Sinf
# ---------------------------------------------------------------------------
@router.callback_query(Reg.grade, F.data.startswith("grd:"))
async def grade_chosen(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(state)
    gid = int(cb.data.split(":")[1])
    async with session_scope() as s:
        grade = await s.get(Grade, gid)
        if not grade or not grade.is_active:
            await cb.answer(t("choose_from_list", lang), show_alert=True)
            return
        subs = await subjects_for_grade(s, gid)
    if not subs:
        await cb.answer(t("no_subjects_for_grade", lang), show_alert=True)
        return
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    await cb.message.answer(f"🎓 <b>{escape(by_lang(grade.name, grade.name_cyr, lang))}</b>")
    data = await state.get_data()
    # Sinf o'zgarsa, yangi sinfga mos kelmaydigan fanlarni olib tashlaymiz va qayta tanlatamiz
    allowed = {x.id for x in subs}
    old = data.get("subjects") or []
    kept = [x for x in old if x in allowed]
    await state.update_data(grade_id=gid, subjects=kept, subjects_dropped=len(kept) != len(old))
    await advance(cb.message, state, "grade")


# ---------------------------------------------------------------------------
# Fanlar (bir yoki bir nechta)
# ---------------------------------------------------------------------------
def _subjects_prompt(lang: str) -> str:
    text = t("ask_subjects", lang)
    mx = st.get("max_subjects")
    if mx and mx > 0:
        text += t("subjects_max_hint", lang).format(max=mx)
    return text


async def send_subjects(msg: Message, state: FSMContext) -> None:
    await state.set_state(Reg.subjects)
    data = await state.get_data()
    lang = data.get("lang", "lat")
    async with session_scope() as s:
        subs = await subjects_for_grade(s, data.get("grade_id") or 0)
    if not subs:
        await msg.answer(t("no_subjects_for_grade", lang))
        await ask(msg, state, "grade")
        return
    await msg.answer(_subjects_prompt(lang), reply_markup=kb.subjects_kb(subs, data.get("subjects") or [], lang))


@router.callback_query(Reg.subjects, F.data.startswith("subj:"))
async def subjects_toggle(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    selected = list(data.get("subjects") or [])
    async with session_scope() as s:
        subs = await subjects_for_grade(s, data.get("grade_id") or 0)
    allowed = {x.id for x in subs}
    selected = [x for x in selected if x in allowed]
    val = cb.data.split(":")[1]

    if val == "done":
        if not selected:
            await cb.answer(t("no_subjects_selected", lang), show_alert=True)
            return
        await cb.answer()
        await safe_edit_markup(cb.message, None)
        names = ", ".join(by_lang(x.name, x.name_cyr, lang) for x in subs if x.id in selected)
        await cb.message.answer(f"📚 <b>{escape(names)}</b>")
        await state.update_data(subjects=selected)
        await advance(cb.message, state, "subjects")
        return

    sid = int(val)
    if sid not in allowed:
        await cb.answer(t("choose_from_list", lang), show_alert=True)
        return
    if sid in selected:
        selected.remove(sid)
    else:
        mx = st.get("max_subjects") or 0
        if mx > 0 and len(selected) >= mx:
            await cb.answer(t("max_subjects", lang).format(max=mx), show_alert=True)
            return
        selected.append(sid)
    await state.update_data(subjects=selected)
    await cb.answer()
    await safe_edit_markup(cb.message, kb.subjects_kb(subs, selected, lang))


# ---------------------------------------------------------------------------
# Qo'shimcha savollar (admin paneldan)
# ---------------------------------------------------------------------------
async def ask_custom(msg: Message, state: FSMContext, idx: int) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    q_ids = data.get("q_ids") or []
    if idx >= len(q_ids):
        await show_confirm(msg, state)
        return
    async with session_scope() as s:
        q = await s.get(CustomQuestion, q_ids[idx])
    if q is None or not q.is_active:
        # Savol o'chirilgan bo'lsa keyingisiga o'tamiz
        await state.update_data(q_ids=[x for x in q_ids if x != q_ids[idx]])
        await ask_custom(msg, state, idx)
        return
    await state.set_state(Reg.custom)
    await state.update_data(q_index=idx, ms_sel=[])
    text = f"❓ <b>{escape(by_lang(q.text, q.text_cyr, lang))}</b>"
    example = by_lang(q.example, q.example_cyr, lang)
    if example:
        text += "\n" + t("example", lang).format(example=escape(example))
    if not q.required:
        text += "\n" + t("optional_hint", lang)
    if q.qtype in ("choice", "multichoice"):
        markup = kb.choice_kb(q_options(q, lang), lang, q.required, multi=q.qtype == "multichoice")
    else:
        markup = None if q.required else kb.skip_kb(lang)
    await msg.answer(text, reply_markup=markup)


async def _current_question(state: FSMContext) -> Optional[CustomQuestion]:
    data = await state.get_data()
    q_ids = data.get("q_ids") or []
    idx = int(data.get("q_index", 0))
    if idx >= len(q_ids):
        return None
    async with session_scope() as s:
        return await s.get(CustomQuestion, q_ids[idx])


async def _save_answer(msg: Message, state: FSMContext, q: CustomQuestion, value: Optional[str]) -> None:
    data = await state.get_data()
    answers = dict(data.get("answers") or {})
    if value is None:
        answers.pop(str(q.id), None)
    else:
        answers[str(q.id)] = value
    await state.update_data(answers=answers)
    if data.get("editing"):
        await show_confirm(msg, state)
    else:
        await ask_custom(msg, state, int(data.get("q_index", 0)) + 1)


@router.message(Reg.custom, F.text)
async def custom_text(msg: Message, state: FSMContext) -> None:
    lang = await get_lang(state)
    q = await _current_question(state)
    if q is None:
        await show_confirm(msg, state)
        return
    if q.qtype in ("choice", "multichoice"):
        await msg.answer(t("choose_from_list", lang))
        return
    raw = msg.text.strip()
    if q.qtype == "number":
        try:
            val = float(raw.replace(",", ".").replace(" ", ""))
        except ValueError:
            await msg.answer(t("bad_number", lang))
            return
        lo = q.min_value if q.min_value is not None else float("-inf")
        hi = q.max_value if q.max_value is not None else float("inf")
        if not lo <= val <= hi:
            await msg.answer(t("bad_number_range", lang).format(
                min=fmt_num(q.min_value) if q.min_value is not None else "−∞",
                max=fmt_num(q.max_value) if q.max_value is not None else "∞"))
            return
        await _save_answer(msg, state, q, fmt_num(val))
    elif q.qtype == "phone":
        phone = normalize_phone(raw)
        if not phone:
            await msg.answer(t("bad_phone", lang))
            return
        await _save_answer(msg, state, q, phone)
    else:
        lo, hi = max(1, q.min_len or 1), max(q.max_len or 200, q.min_len or 1)
        if not lo <= len(raw) <= hi:
            await msg.answer(t("bad_text_len", lang).format(min=lo, max=hi))
            return
        await _save_answer(msg, state, q, raw)


@router.callback_query(Reg.custom, F.data.startswith("cq:"))
async def custom_choice(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    q = await _current_question(state)
    if q is None:
        await cb.answer()
        await show_confirm(cb.message, state)
        return
    val = cb.data.split(":", 1)[1]
    opts_lat = q.option_list()

    if val == "skip":
        if q.required:
            await cb.answer(t("choose_from_list", lang), show_alert=True)
            return
        await cb.answer()
        await safe_edit_markup(cb.message, None)
        await _save_answer(cb.message, state, q, None)
        return

    if q.qtype == "multichoice":
        sel = list(data.get("ms_sel") or [])
        if val == "done":
            if not sel:
                if q.required:
                    await cb.answer(t("choose_at_least_one", lang), show_alert=True)
                    return
                await cb.answer()
                await safe_edit_markup(cb.message, None)
                await _save_answer(cb.message, state, q, None)
                return
            await cb.answer()
            await safe_edit_markup(cb.message, None)
            chosen = [opts_lat[i] for i in sorted(sel) if i < len(opts_lat)]
            labels = [q_options(q, lang)[i] for i in sorted(sel) if i < len(opts_lat)]
            await cb.message.answer("☑️ " + escape(", ".join(labels)))
            await _save_answer(cb.message, state, q, "; ".join(chosen))
            return
        i = int(val)
        if i >= len(opts_lat):
            await cb.answer()
            return
        sel.remove(i) if i in sel else sel.append(i)
        await state.update_data(ms_sel=sel)
        await cb.answer()
        await safe_edit_markup(cb.message, kb.choice_kb(q_options(q, lang), lang, q.required,
                                                        multi=True, selected=sel))
        return

    if not val.isdigit() or int(val) >= len(opts_lat):
        await cb.answer()
        return
    i = int(val)
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    await cb.message.answer("☑️ " + escape(q_options(q, lang)[i]))
    await _save_answer(cb.message, state, q, opts_lat[i])


# ---------------------------------------------------------------------------
# Tasdiqlash va tahrirlash
# ---------------------------------------------------------------------------
async def find_missing(session, data: dict) -> Optional[tuple[str, int]]:
    """To'ldirilmagan yoki eskirgan ma'lumot: (bosqich, savol_indeksi)."""
    for f in ("first_name", "last_name", "phone"):
        if not data.get(f):
            return f, 0
    school = await session.get(School, data.get("school_id") or 0)
    if not school or not school.is_active:
        return "school", 0
    if not data.get("address"):
        return "address", 0
    grade = await session.get(Grade, data.get("grade_id") or 0)
    if not grade or not grade.is_active:
        return "grade", 0
    allowed = {x.id for x in await subjects_for_grade(session, grade.id)}
    chosen = data.get("subjects") or []
    mx = st.get("max_subjects") or 0
    if not chosen or not set(chosen) <= allowed or (mx > 0 and len(chosen) > mx):
        return "subjects", 0
    channels = await active_channels(session)
    if instagram_required(channels) and not data.get("instagram"):
        return "instagram", 0
    qs = await active_questions(session)
    answers = data.get("answers") or {}
    for i, q in enumerate(qs):
        if q.required and not answers.get(str(q.id)):
            return "custom", i
    return None


async def show_confirm(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    async with session_scope() as s:
        qs = await active_questions(s)
        await state.update_data(q_ids=[q.id for q in qs])
        missing = await find_missing(s, data)
        if missing:
            step, idx = missing
            await state.update_data(editing=True)
            if step == "custom":
                await ask_custom(msg, state, idx)
            elif step == "instagram":
                await state.set_state(Reg.instagram)
                await msg.answer(t("ask_instagram", lang), reply_markup=kb.nav_kb(lang))
            else:
                await ask(msg, state, step)
            return
        summary = await summary_from_data(s, data, lang)
    await state.set_state(Reg.confirm)
    await msg.answer(t("confirm", lang).format(summary=summary), reply_markup=kb.confirm_kb(lang))


@router.callback_query(Reg.confirm, F.data == "conf:edit")
async def confirm_edit(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(state)
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    async with session_scope() as s:
        qs = await active_questions(s)
        channels = await active_channels(s)
    await state.set_state(Reg.edit_menu)
    await cb.message.answer(t("edit_choose", lang),
                            reply_markup=kb.edit_menu_kb(lang, qs, instagram_required(channels)))


@router.callback_query(Reg.edit_menu, F.data.startswith("ed:"))
async def edit_field(cb: CallbackQuery, state: FSMContext) -> None:
    lang = await get_lang(state)
    await cb.answer()
    await safe_edit_markup(cb.message, None)
    field = cb.data[3:]
    if field == "back":
        await show_confirm(cb.message, state)
        return
    await state.update_data(editing=True)
    if field == "instagram":
        await state.set_state(Reg.instagram)
        await cb.message.answer(t("ask_instagram", lang), reply_markup=kb.nav_kb(lang))
        return
    if field.startswith("q:"):
        qid = int(field[2:])
        async with session_scope() as s:
            qs = await active_questions(s)
        ids = [q.id for q in qs]
        await state.update_data(q_ids=ids)
        if qid in ids:
            await ask_custom(cb.message, state, ids.index(qid))
        else:
            await show_confirm(cb.message, state)
        return
    if field in STEPS:
        await ask(cb.message, state, field)


@router.callback_query(Reg.confirm, F.data == "conf:yes")
async def confirm_yes(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    lang = data.get("lang", "lat")
    mode = data.get("mode", "new")
    user = cb.from_user
    await cb.answer()

    async with session_scope() as s:
        existing = await psvc.by_telegram(s, user.id)

        # 1) Muddat va huquq tekshiruvi
        if mode == "update":
            if existing is None or not await can_edit_profile(s, existing):
                await safe_edit_markup(cb.message, None)
                await state.set_state(None)
                await state.set_data({"lang": lang})
                await cb.message.answer(t("edit_closed", lang))
                return
        else:
            if existing is not None:
                await safe_edit_markup(cb.message, None)
                await state.set_state(None)
                await state.set_data({"lang": existing.lang})
                from .menu import show_menu
                await show_menu(cb.message, state)
                return
            rs = st.registration_state()
            if rs != "open":
                await safe_edit_markup(cb.message, None)
                await cb.message.answer(t("reg_closed" if rs == "closed" else "reg_not_started", lang)
                                        .format(start=fmt_dt(st.get("registration_start"))))
                return
            # 2) Obunani yana bir bor tekshiramiz (o'quvchi chiqib ketgan bo'lishi mumkin)
            channels = await active_channels(s)
            if await missing_channels(bot, user.id, channels):
                await safe_edit_markup(cb.message, None)
                await state.update_data(resub=True)
                await cb.message.answer(t("lost_subscription", lang))
                await show_subscribe(cb.message, state)
                return

        # 3) To'liqlik
        missing = await find_missing(s, data)
        if missing:
            await safe_edit_markup(cb.message, None)
            await show_confirm(cb.message, state)
            return

        # 4) Telefon bandligi
        if await psvc.phone_taken(s, data["phone"], exclude_telegram_id=user.id):
            await safe_edit_markup(cb.message, None)
            await state.update_data(editing=True)
            await cb.message.answer(t("phone_taken", lang))
            await ask(cb.message, state, "phone")
            return

        # 5) Saqlash. Barcha so'rovlar OLDIN bajariladi: async rejimda obyekt sessiyaga
        # qo'shilgandan keyin bog'lanishlarga yashirin murojaat bo'lmasligi kerak.
        subjects = list((await s.execute(select(Subject).where(Subject.id.in_(data["subjects"])))).scalars().all())
        valid_q = {q.id for q in await active_questions(s)}
        answers = {int(k): v for k, v in (data.get("answers") or {}).items() if v and int(k) in valid_q}
        fields = dict(
            tg_username=user.username or "", lang=lang,
            first_name=data["first_name"], last_name=data["last_name"], phone=data["phone"],
            school_id=data["school_id"], grade_id=data["grade_id"], address=data["address"],
            instagram_username=data.get("instagram", "") or "",
        )
        if mode == "update":
            p = existing
            for k, v in fields.items():
                setattr(p, k, v)
            p.subjects = subjects
            # Javoblar: mavjudlarini yangilaymiz, keraksizlarini o'chiramiz, yangilarini qo'shamiz
            for a in list(p.answers):
                if a.question_id in answers:
                    a.value = answers.pop(a.question_id)
                else:
                    p.answers.remove(a)
            for qid, val in answers.items():
                p.answers.append(CustomAnswer(question_id=qid, value=val))
        else:
            p = Participant(telegram_id=user.id, subjects=subjects,
                            answers=[CustomAnswer(question_id=q, value=v) for q, v in answers.items()],
                            **fields)
            s.add(p)

        try:
            await s.flush()
            if not p.code:
                psvc.assign_code(p)
            p.search_text = psvc.build_search_text(p)
            await s.commit()
        except IntegrityError:
            await s.rollback()
            log.info("IntegrityError saqlashda (user=%s)", user.id)
            async with session_scope() as s2:
                if await psvc.by_telegram(s2, user.id) and mode != "update":
                    await state.set_state(None)
                    await state.set_data({"lang": lang})
                    from .menu import show_menu
                    await safe_edit_markup(cb.message, None)
                    await show_menu(cb.message, state)
                    return
            await safe_edit_markup(cb.message, None)
            await state.update_data(editing=True)
            await cb.message.answer(t("phone_taken", lang))
            await ask(cb.message, state, "phone")
            return
        code = p.code
        can_edit = await can_edit_profile(s, p)
    ranking.invalidate()

    await safe_edit_markup(cb.message, None)
    await state.set_state(None)
    await state.set_data({"lang": lang})
    if mode == "update":
        await cb.message.answer(t("updated", lang), reply_markup=kb.menu_kb(lang, can_edit))
        return
    await stickers.send(cb.bot, cb.message.chat.id, "sticker_congrats")
    congrats = by_lang(st.get("congrats_text"), st.get("congrats_text_cyr"), lang)
    if congrats.strip():
        await cb.message.answer(congrats, parse_mode=None, disable_web_page_preview=True)
    await cb.message.answer(t("saved_code", lang).format(code=code), reply_markup=kb.menu_kb(lang, can_edit))
    await _milestone()


async def _milestone() -> None:
    """Har N-o'quvchida adminlarga xabar (masalan, 100, 200, ...)."""
    every = st.get("notify_every") or 0
    if every <= 0:
        return
    async with session_scope() as s:
        total = (await s.execute(select(func.count()).select_from(Participant))).scalar_one()
    if total and total % every == 0:
        from ...services import notify
        await notify.send(f"🎉 Ro'yxatdan o'tganlar soni <b>{total}</b> taga yetdi!\n📊 Batafsil: /stat")


# Bosqichga mos bo'lmagan xabarlar (masalan, tugma o'rniga matn yozilsa)
@router.message(StateFilter(Reg.grade, Reg.subjects, Reg.confirm, Reg.edit_menu))
async def need_buttons(msg: Message, state: FSMContext) -> None:
    await msg.answer(t("choose_from_list", await get_lang(state)))


@router.message(StateFilter(Reg.first_name, Reg.last_name, Reg.phone, Reg.school, Reg.address,
                            Reg.custom, Reg.instagram))
async def need_text(msg: Message, state: FSMContext) -> None:
    """Rasm, stiker va h.k. yuborilsa — savolni qayta beramiz."""
    cur = (await state.get_state() or "").split(":")[-1]
    data = await state.get_data()
    if cur == "custom":
        await ask_custom(msg, state, int(data.get("q_index", 0)))
    elif cur == "instagram":
        await msg.answer(t("ask_instagram", await get_lang(state)))
    else:
        await ask(msg, state, cur)


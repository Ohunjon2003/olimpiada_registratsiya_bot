"""Bot tugmalari."""
from __future__ import annotations

from typing import Iterable, Sequence

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup,
                           ReplyKeyboardRemove)

from ..services import settings as st
from ..utils import by_lang, now_utc
from .texts import t

SCHOOLS_PER_PAGE = 10


def ib(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def lang_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(t("btn_lat"), "lang:lat")],
        [ib(t("btn_cyr"), "lang:cyr")],
    ])


def nav_kb(lang: str, back: bool = True) -> ReplyKeyboardMarkup:
    """Matn kiritiladigan bosqichlar uchun: Orqaga / Bekor qilish."""
    row = [KeyboardButton(text=t("btn_back", lang))] if back else []
    row.append(KeyboardButton(text=t("btn_cancel", lang)))
    return ReplyKeyboardMarkup(keyboard=[row], resize_keyboard=True)


def phone_kb(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t("btn_send_contact", lang), request_contact=True)],
        [KeyboardButton(text=t("btn_back", lang)), KeyboardButton(text=t("btn_cancel", lang))],
    ], resize_keyboard=True)


def menu_kb(lang: str, can_edit: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=t("btn_profile", lang)), KeyboardButton(text=t("btn_results", lang))],
        [KeyboardButton(text=t("btn_cert", lang)), KeyboardButton(text=t("btn_lang", lang))],
    ]
    extra = []
    if st.get("passes_published"):
        extra.append(KeyboardButton(text=t("btn_pass", lang)))
    if can_edit:
        extra.append(KeyboardButton(text=t("btn_edit_profile", lang)))
    if appeals_open():
        extra.append(KeyboardButton(text=t("btn_appeal", lang)))
    if extra:
        rows.append(extra)
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def appeals_open() -> bool:
    """E'tiroz qabul qilinyaptimi: admin yoqqan, natijalar e'lon qilingan va muddat o'tmagan."""
    if not (st.get("appeals_enabled") and st.get("results_published")):
        return False
    until = st.get("appeals_until")
    return until is None or now_utc() < until


def remove_kb() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def channels_kb(channels: Sequence, lang: str) -> InlineKeyboardMarkup:
    icons = {"telegram": "📢", "instagram": "📸"}
    rows = [[InlineKeyboardButton(text=f"{icons.get(c.kind, '🔗')} {by_lang(c.title, c.title_cyr, lang)}",
                                  url=c.url)] for c in channels]
    rows.append([ib(t("btn_check", lang), "sub:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rules_ok_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib(t("btn_rules_ok", lang), "rules:ok")]])


def schools_kb(schools: Sequence, lang: str, page: int, total: int,
               searching: bool = False) -> InlineKeyboardMarkup:
    rows = [[ib(by_lang(s.name, s.name_cyr, lang), f"sch:{s.id}")] for s in schools]
    pages = max(1, (total + SCHOOLS_PER_PAGE - 1) // SCHOOLS_PER_PAGE)
    if not searching and pages > 1:
        nav = []
        nav.append(ib("◀️", f"schp:{page - 1}") if page > 0 else ib("·", "noop"))
        nav.append(ib(f"{page + 1}/{pages}", "noop"))
        nav.append(ib("▶️", f"schp:{page + 1}") if page < pages - 1 else ib("·", "noop"))
        rows.append(nav)
    if searching:
        rows.append([ib("📋 " + ("Бутун рўйхат" if lang == "cyr" else "Butun ro'yxat"), "schp:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def grades_kb(grades: Sequence, lang: str) -> InlineKeyboardMarkup:
    rows, row = [], []
    for g in grades:
        row.append(ib(by_lang(g.name, g.name_cyr, lang), f"grd:{g.id}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def subjects_kb(subjects: Sequence, selected: Iterable[int], lang: str) -> InlineKeyboardMarkup:
    sel = set(selected)
    rows = [[ib(("✅ " if s.id in sel else "▫️ ") + by_lang(s.name, s.name_cyr, lang), f"subj:{s.id}")]
            for s in subjects]
    rows.append([ib(t("btn_continue", lang), "subj:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def choice_kb(options: Sequence[str], lang: str, required: bool,
              multi: bool = False, selected: Iterable[int] = ()) -> InlineKeyboardMarkup:
    sel = set(selected)
    rows = []
    for i, o in enumerate(options):
        label = (("✅ " if i in sel else "▫️ ") + o) if multi else o
        rows.append([ib(label, f"cq:{i}")])
    if multi:
        rows.append([ib(t("btn_done", lang), "cq:done")])
    if not required:
        rows.append([ib(t("btn_skip", lang), "cq:skip")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def skip_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[ib(t("btn_skip", lang), "cq:skip")]])


def confirm_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [ib(t("btn_confirm", lang), "conf:yes")],
        [ib(t("btn_edit", lang), "conf:edit")],
    ])


def edit_menu_kb(lang: str, questions: Sequence, with_instagram: bool) -> InlineKeyboardMarkup:
    fields = ["first_name", "last_name", "phone", "school", "address", "grade", "subjects"]
    rows, row = [], []
    for f in fields:
        row.append(ib(t(f"f_{f}", lang), f"ed:{f}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if with_instagram:
        row.append(ib(t("f_instagram", lang), "ed:instagram"))
    if row:
        rows.append(row)
    for q in questions:
        title = by_lang(q.text, q.text_cyr, lang)
        rows.append([ib("❓ " + (title[:40] + "…" if len(title) > 40 else title), f"ed:q:{q.id}")])
    rows.append([ib(t("btn_back", lang), "ed:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

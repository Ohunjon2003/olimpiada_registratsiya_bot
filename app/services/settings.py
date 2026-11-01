"""Sozlamalar xizmati: bazadagi 'settings' jadvali + xotiradagi kesh.

Loyiha bitta jarayonda ishlaydi (bot + web), shuning uchun keshni
o'zgartirishda yangilash yetarli.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Setting
from ..utils import now_utc

# kalit: (turi, standart qiymat, izoh)
DEFAULTS: dict[str, tuple[str, Any, str]] = {
    "olympiad_name": ("str", "Fan olimpiadasi", "Olimpiada nomi"),
    "registration_start": ("datetime", None, "Ro'yxatdan o'tish boshlanishi"),
    "registration_end": ("datetime", None, "Ro'yxatdan o'tish tugashi"),
    "instagram_username_required": ("bool", False, "Instagram username so'ralsin"),
    "max_subjects": ("int", 0, "Bir o'quvchi tanlashi mumkin bo'lgan fanlar soni (0 — cheklanmagan)"),
    "allow_profile_edit": ("bool", True, "Ro'yxatdan o'tgandan keyin ma'lumotni o'zgartirish (muddat ichida)"),
    "judges_see_names": ("bool", False, "Hakamlar ishtirokchi ismini ko'rsin"),
    "judges_attendance": ("bool", False, "Hakamlar davomatni belgilay olsin"),
    "results_published": ("bool", False, "Natijalar e'lon qilingan (bot orqali ko'rinadi)"),
    "ranking_by_grade": ("bool", True, "O'quvchilarga o'rin sinf kesimida beriladi"),
    "certificates_enabled": ("bool", False, "Sertifikat/diplomlar yoqilgan"),
    # Salomlashish va tabrik matnlari «Bot matnlari» sahifasida tahrirlanadi
    "welcome_text": ("text", "👋 Assalomu alaykum!\n\n🏆 {olympiad} ga ro'yxatdan o'tish botiga "
                             "xush kelibsiz!\n\n📝 Ro'yxatdan o'tish 2–3 daqiqa vaqt oladi. Boshladik! 🚀",
                     "Salomlashish matni"),
    "welcome_text_cyr": ("text", "", "Salomlashish matni (kirill)"),
    "congrats_text": ("text",
                      "🎉🎉🎉 Tabriklaymiz! Siz olimpiadaga muvaffaqiyatli ro'yxatdan o'tdingiz! 🥳\n\n"
                      "📅 Olimpiada vaqti va joyi haqida shu bot orqali xabar beramiz.\n"
                      "📞 Savollar uchun: +998 90 000 00 00\n\n"
                      "🍀 Omad tilaymiz!", "Tabrik va reklama matni"),
    "congrats_text_cyr": ("text", "", "Tabrik matni (kirill)"),
    # Stikerlar: "" — o'chiq; oddiy emoji (Telegram uni katta animatsiya qilib ko'rsatadi)
    # yoki JSON {"type": "sticker", "file_id": ..., "preview": ..., "emoji": ...}
    "sticker_welcome": ("sticker", "👋", "Salomlashishda (/start)"),
    "sticker_congrats": ("sticker", "🎉", "Ro'yxatdan o'tib bo'lgach"),
    "sticker_results": ("sticker", "🏆", "Natijalar e'lon qilinganda"),
    "backup_interval_hours": ("int", 24, "Avtomatik zaxira oralig'i (soat)"),
    # --- Admin Telegram xabarnomalari («🔔 Telegram xabarnomalar» sahifasi) ---
    "admin_chats": ("json", "[]", "Ulangan admin Telegram akkauntlari"),
    "notify_every": ("int", 100, "Har necha yangi o'quvchida xabar berish (0 — yo'q)"),
    "notify_errors": ("bool", True, "Bot/server xatolari va kanal muammolari haqida xabar berish"),
    "backup_to_telegram": ("bool", True, "Avtomatik zaxira nusxani Telegram'ga yuborish"),
    # --- E'tirozlar («⚖️ E'tirozlar» sahifasi) ---
    "appeals_enabled": ("bool", False, "O'quvchilar natijaga e'tiroz bildira oladi"),
    "appeals_until": ("datetime", None, "E'tiroz qabul qilish muddati"),
    # --- Joylashtirish va ruxsatnoma («🪑 Joylashtirish» sahifasi) ---
    "seating_one_per_desk": ("bool", False, "Har partaga 1 kishi (2 kishilik partalarda)"),
    "seating_group_by": ("str", "none", "Smena/xonalarni guruhlash: none | grade | subject"),
    "pass_notice": ("bool", False, "Ro'yxatdan o'tganda ruxsatnoma haqida xabar berish"),
    "passes_published": ("bool", False, "Botda «🎫 Ruxsatnoma» tugmasi ko'rinadi"),
    "pass_send_time": ("str", "18:00", "Ruxsatnoma yuboriladigan vaqt (olimpiadadan bir kun oldin)"),
    # --- Avtomatik tekshiruv («📝 Titul va skaner» sahifasi) ---
    "omr_id_required": ("bool", True, "O'quvchi ID belgilamagan titul admin tasdig'iga tushadi"),
    "omr_show_name": ("bool", False, "Titulda o'quvchi ismi yozilsin"),
}

_cache: dict[str, str] = {}
_loaded = False


async def load(session: AsyncSession) -> None:
    """Barcha sozlamalarni keshga yuklaydi."""
    global _loaded
    rows = (await session.execute(select(Setting))).scalars().all()
    _cache.clear()
    _cache.update({r.key: r.value for r in rows})
    _loaded = True


def _convert(key: str, raw: str | None) -> Any:
    kind, default, _ = DEFAULTS.get(key, ("str", "", ""))
    if raw is None:
        return default
    if kind == "bool":
        return raw == "1"
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            return default
    if kind == "datetime":
        try:
            return datetime.fromisoformat(raw) if raw else None
        except ValueError:
            return None
    return raw


# Eski standart matnlar: admin ularni o'zgartirmagan bo'lsa, yangisiga almashtiriladi
OLD_DEFAULTS: dict[str, list[str]] = {
    "welcome_text": ["Assalomu alaykum! {olympiad} ga ro'yxatdan o'tish botiga xush kelibsiz."],
    "congrats_text": ["🎉 Tabriklaymiz! Siz olimpiadaga muvaffaqiyatli ro'yxatdan o'tdingiz.\n\n"
                      "Savollar uchun: +998 90 000 00 00"],
}

# «Umumiy sozlamalar» formasida yo'q (boshqa sahifada tahrirlanadigan) kalitlar
TEXT_PAGE_KEYS = ("welcome_text", "welcome_text_cyr", "congrats_text", "congrats_text_cyr",
                  "sticker_welcome", "sticker_congrats", "sticker_results",
                  "admin_chats", "notify_every", "notify_errors", "backup_to_telegram",
                  "appeals_enabled", "appeals_until", "seating_one_per_desk", "seating_group_by",
                  "pass_notice", "passes_published", "pass_send_time", "omr_id_required", "omr_show_name")


def raw(key: str) -> str:
    """DEFAULTS'da yo'q erkin kalit (masalan, bot matni 'txt.ask_phone') qiymati."""
    return _cache.get(key) or ""


def get(key: str) -> Any:
    """Sozlama qiymati (keshdan)."""
    return _convert(key, _cache.get(key))


async def set_many(session: AsyncSession, values: dict[str, Any]) -> None:
    """Bir nechta sozlamani saqlaydi va keshni yangilaydi."""
    for key, val in values.items():
        kind = DEFAULTS.get(key, ("str",))[0]
        if kind == "bool":
            raw = "1" if val else "0"
        elif kind == "datetime":
            raw = val.isoformat() if val else ""
        else:
            raw = "" if val is None else str(val)
        obj = await session.get(Setting, key)
        if obj is None:
            session.add(Setting(key=key, value=raw))
        else:
            obj.value = raw
        _cache[key] = raw
    await session.commit()


def registration_state() -> str:
    """'open' | 'not_started' | 'closed' — ro'yxatdan o'tish holati."""
    now = now_utc()
    start, end = get("registration_start"), get("registration_end")
    if start and now < start:
        return "not_started"
    if end and now > end:
        return "closed"
    return "open"

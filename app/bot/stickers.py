"""Stikerlar: botga yuborilgan stikerlarni eslab qolish va kerakli joyda yuborish.

Admin stikerni tanlash uchun uni o'z telefonidan botga yuboradi — bot oxirgi
stikerlarni xotirada saqlaydi, web panelda esa ulardan biri tanlanadi.

Sozlama qiymati (sticker_welcome va h.k.):
  ""                 — o'chiq
  "🎉"               — oddiy emoji (Telegram yakka emojini katta animatsiya bilan ko'rsatadi)
  '{"type": "sticker", "file_id": ..., "preview": ..., "emoji": ...}' — Telegram stikeri
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.types import Sticker

from ..services import settings as st

log = logging.getLogger("bot.stickers")

SLOTS = ("sticker_welcome", "sticker_congrats", "sticker_results")

# file_unique_id → ma'lumot; eng yangisi oxirida. Faqat xotirada (qayta ishga tushsa tozalanadi).
_recent: "OrderedDict[str, dict]" = OrderedDict()
_MAX_RECENT = 30


def record(sticker: Sticker, user_id: int) -> None:
    """Botga kelgan stikerni ro'yxatga qo'shadi (admin panelda tanlash uchun)."""
    preview = None
    if sticker.thumbnail:
        preview = sticker.thumbnail.file_id
    elif not sticker.is_animated and not sticker.is_video:
        preview = sticker.file_id  # oddiy (webp) stiker — o'zini ko'rsatish mumkin
    item = {"file_id": sticker.file_id, "preview": preview, "emoji": sticker.emoji or "",
            "set_name": sticker.set_name or "", "user_id": user_id,
            "time": datetime.now().strftime("%H:%M")}
    _recent.pop(sticker.file_unique_id, None)
    _recent[sticker.file_unique_id] = item
    while len(_recent) > _MAX_RECENT:
        _recent.popitem(last=False)


def recent() -> list[dict]:
    """Oxirgi stikerlar — eng yangisi birinchi."""
    return list(reversed(_recent.values()))


def parse(value: str) -> Optional[dict]:
    """Sozlama qiymatini o'qiydi: None | {"type": "emoji", "value"} | {"type": "sticker", ...}."""
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("{"):
        try:
            data = json.loads(value)
            if data.get("type") == "sticker" and data.get("file_id"):
                return data
        except ValueError:
            pass
        return None
    return {"type": "emoji", "value": value}


def make_sticker_value(file_id: str, preview: Optional[str], emoji: str) -> str:
    return json.dumps({"type": "sticker", "file_id": file_id, "preview": preview, "emoji": emoji},
                      ensure_ascii=False)


async def send(bot: Optional[Bot], chat_id: int, slot: str) -> bool:
    """Sozlangan stiker/emojini yuboradi. Hech qachon xato chiqarmaydi (asosiy jarayonni buzmasin)."""
    if bot is None:
        return False
    data = parse(st.get(slot) or "")
    if data is None:
        return False
    try:
        if data["type"] == "sticker":
            await bot.send_sticker(chat_id, data["file_id"])
        else:
            await bot.send_message(chat_id, data["value"], parse_mode=None)
        return True
    except Exception as e:  # stiker o'chirilgan, foydalanuvchi bloklagan va h.k.
        log.warning("Stiker (%s) yuborilmadi: %s", slot, e)
        return False

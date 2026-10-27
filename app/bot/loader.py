"""Bot va dispatcher obyektlari (bitta nusxa, web qism ham shundan foydalanadi)."""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from ..config import config
from .storage import SQLiteStorage

log = logging.getLogger("bot")

bot: Optional[Bot] = None
dp: Optional[Dispatcher] = None


def create() -> tuple[Optional[Bot], Dispatcher]:
    """Bot va dispatcher yaratadi. Token bo'lmasa bot None bo'ladi (web baribir ishlaydi)."""
    global bot, dp
    from .handlers import setup_routers  # aylanma importdan qochish

    dp = Dispatcher(storage=SQLiteStorage())
    setup_routers(dp)
    if config.BOT_TOKEN:
        bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    else:
        log.warning("BOT_TOKEN berilmagan — bot ishga tushmaydi, faqat web panel ishlaydi.")
        bot = None
    return bot, dp

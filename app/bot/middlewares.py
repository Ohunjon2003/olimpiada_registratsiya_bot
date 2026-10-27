"""Bitta foydalanuvchining xabarlarini ketma-ket qayta ishlash.

aiogram yangilanishlarni parallel bajaradi — bu ko'p o'quvchi bir vaqtda
ro'yxatdan o'tganda tezlik beradi. Lekin bitta o'quvchi tugmalarni juda tez
bossa (masalan, fanlarni belgilashda), uning holati (FSM) bir-birini
bosib ketishi mumkin. Shu sababli har bir foydalanuvchi uchun alohida qulf:
turli o'quvchilar parallel, bitta o'quvchi esa navbat bilan.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

log = logging.getLogger("bot")


def _describe(event: TelegramObject) -> str:
    """Jurnal uchun qisqa tavsif: xabar matni yoki tugma ma'lumoti."""
    if isinstance(event, Update):
        if event.message:
            m = event.message
            return "xabar: " + (m.text or ("kontakt" if m.contact else m.content_type))[:40]
        if event.callback_query:
            return "tugma: " + (event.callback_query.data or "")[:40]
    return type(event).__name__


class PerUserLock(BaseMiddleware):
    def __init__(self) -> None:
        self._locks: dict[int, list] = {}  # user_id -> [Lock, kutayotganlar soni]

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        log.info("Bot ← %s (%s)", user.id, _describe(event))
        if isinstance(event, Update) and event.message and event.message.sticker:
            from .stickers import record
            record(event.message.sticker, user.id)  # admin panelda tanlash uchun
        t0 = time.monotonic()
        entry = self._locks.setdefault(user.id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                return await handler(event, data)
        finally:
            dt = time.monotonic() - t0
            if dt > 5:
                log.warning("Bot: %s ning so'rovi %.1f s davom etdi", user.id, dt)
            entry[1] -= 1
            if entry[1] == 0:
                self._locks.pop(user.id, None)  # xotira to'lib ketmasligi uchun

"""Adminlarga Telegram orqali xabar berish.

Admin o'z Telegram akkauntini panel orqali ulaydi: panel bir martalik kod beradi,
admin botga «/link KOD» yuboradi. Ulangan akkauntlar 'admin_chats' sozlamasida
(JSON ro'yxat) saqlanadi.

Bu yerdagi funksiyalar hech qachon xato chiqarmaydi — xabarnoma yuborilmay qolsa ham
asosiy ish (ro'yxatdan o'tish, zaxira va h.k.) to'xtamasligi kerak.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
import zipfile
from html import escape
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from . import settings as st

log = logging.getLogger("notify")

# --- Bir martalik ulash kodlari: kod → (amal qilish muddati, kim yaratgan) ---
_codes: dict[str, tuple[float, str]] = {}
CODE_TTL = 15 * 60

# --- Bir xil ogohlantirishni tez-tez yubormaslik uchun: kalit → oxirgi yuborilgan vaqt ---
_last_sent: dict[str, float] = {}


def admin_chats() -> list[dict]:
    """[{"id": 123, "name": "Ali", "by": "admin"}, ...]"""
    try:
        data = json.loads(st.get("admin_chats") or "[]")
        return [x for x in data if isinstance(x, dict) and isinstance(x.get("id"), int)]
    except ValueError:
        return []


def is_admin_chat(chat_id: int) -> bool:
    return any(x["id"] == chat_id for x in admin_chats())


def new_code(username: str) -> str:
    """Panel uchun yangi 6 xonali kod (15 daqiqa amal qiladi)."""
    now = time.time()
    for k in [k for k, (exp, _) in _codes.items() if exp < now]:
        _codes.pop(k, None)
    code = f"{secrets.randbelow(900000) + 100000}"
    _codes[code] = (now + CODE_TTL, username)
    return code


def consume_code(code: str) -> Optional[str]:
    """Kod to'g'ri bo'lsa — uni yaratgan admin nomi (kod bir martalik)."""
    item = _codes.pop(code.strip(), None)
    if item is None or item[0] < time.time():
        return None
    return item[1]


async def add_chat(session: AsyncSession, chat_id: int, name: str, by: str) -> None:
    chats = [x for x in admin_chats() if x["id"] != chat_id]
    chats.append({"id": chat_id, "name": name[:64], "by": by})
    await st.set_many(session, {"admin_chats": json.dumps(chats, ensure_ascii=False)})


async def remove_chat(session: AsyncSession, chat_id: int) -> None:
    chats = [x for x in admin_chats() if x["id"] != chat_id]
    await st.set_many(session, {"admin_chats": json.dumps(chats, ensure_ascii=False)})


def _bot():
    from ..bot import loader
    return loader.bot


async def send(text: str, throttle_key: Optional[str] = None, every: int = 600) -> int:
    """Barcha ulangan adminlarga HTML xabar. throttle_key berilsa — 'every' soniyada ko'pi bilan bir marta.

    Qaytaradi: nechta adminga yetkazildi.
    """
    bot, chats = _bot(), admin_chats()
    if bot is None or not chats:
        return 0
    if throttle_key:
        now = time.time()
        if now - _last_sent.get(throttle_key, 0) < every:
            return 0
        _last_sent[throttle_key] = now
    ok = 0
    for c in chats:
        try:
            await bot.send_message(c["id"], text[:4000], disable_web_page_preview=True)
            ok += 1
        except Exception as e:
            log.warning("Adminga (%s) xabar yuborilmadi: %s", c["id"], e)
    return ok


async def error(title: str, detail: str, key: str, every: int = 600) -> None:
    """Xato haqida ogohlantirish (sozlamada yoqilgan bo'lsa)."""
    if not st.get("notify_errors"):
        return
    await send(f"⚠️ <b>{escape(title)}</b>\n<code>{escape(detail[:1500])}</code>", throttle_key=key, every=every)


# Telegram botlar yuborishi mumkin bo'lgan fayl hajmi chegarasi — 50 MB
_MAX_DOC = 49 * 1024 * 1024


def _zip(path: Path) -> Path:
    """Zaxira faylni siqadi (SQLite fayli 5–10 barobar kichrayadi)."""
    out = path.with_suffix(".zip")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.write(path, arcname=path.name)
    return out


async def send_backup(path: Path, caption: str = "") -> int:
    """Zaxira nusxani adminlarga Telegram'da yuboradi. Qaytaradi: nechta adminga yetkazildi."""
    import asyncio
    from aiogram.types import FSInputFile

    bot, chats = _bot(), admin_chats()
    if bot is None or not chats:
        return 0
    zpath = await asyncio.to_thread(_zip, path)
    try:
        if zpath.stat().st_size > _MAX_DOC:
            await send(f"⚠️ Zaxira nusxa juda katta ({zpath.stat().st_size // 1024 // 1024} MB) — "
                       f"Telegram orqali yuborib bo'lmaydi. Uni paneldan yuklab oling.")
            return 0
        ok, file_id = 0, None
        for c in chats:
            try:
                msg = await bot.send_document(c["id"], file_id or FSInputFile(zpath),
                                              caption=caption[:1000] or None)
                if msg.document:
                    file_id = msg.document.file_id  # keyingi adminlarga qayta yuklamaslik uchun
                ok += 1
            except Exception as e:
                log.warning("Zaxira adminga (%s) yuborilmadi: %s", c["id"], e)
        return ok
    finally:
        try:
            zpath.unlink()
        except OSError:
            pass

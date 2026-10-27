"""Bazaning zaxira nusxasi.

SQLite'ning o'z 'backup' API'si ishlatiladi — baza ishlab turganda ham
to'g'ri (buzilmagan) nusxa olinadi.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import config
from . import settings as st

log = logging.getLogger("backup")


def make_backup(tag: str = "auto") -> Path:
    """Zaxira nusxa yaratadi va eski nusxalarni o'chiradi."""
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(config.TZ).strftime("%Y%m%d_%H%M%S")
    dest = config.BACKUP_DIR / f"olimpiada_{stamp}_{tag}.db"
    src = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    # Eng so'nggi BACKUP_KEEP ta nusxani qoldiramiz
    files = sorted(config.BACKUP_DIR.glob("olimpiada_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[config.BACKUP_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass
    log.info("Zaxira nusxa: %s", dest.name)
    return dest


def list_backups() -> list[Path]:
    return sorted(config.BACKUP_DIR.glob("olimpiada_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)


async def backup_loop() -> None:
    """Har 5 daqiqada tekshiradi: oxirgi nusxa sozlamadagi oraliqdan eski bo'lsa — yangisini oladi."""
    while True:
        try:
            hours = max(1, int(st.get("backup_interval_hours") or 24))
            last = list_backups()
            age = (datetime.now().timestamp() - last[0].stat().st_mtime) if last else None
            if age is None or age >= hours * 3600:
                path = await asyncio.to_thread(make_backup, "auto")
                if st.get("backup_to_telegram"):
                    from . import notify
                    await notify.send_backup(path, f"💾 Avtomatik zaxira nusxa: {path.name}\n"
                                                   f"Tiklash: README, 7-bo'lim.")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Zaxira olishda xato")
            from . import notify
            await notify.error("Zaxira olishda xato", repr(e), key="backup", every=3600)
        await asyncio.sleep(300)

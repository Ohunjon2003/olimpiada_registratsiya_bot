"""Loyiha sozlamalari: .env faylidan o'qiladi.

Maxfiy qiymatlar (bot token, maxfiy kalit, admin paroli) hech qachon kodga
yozilmaydi — faqat .env faylda saqlanadi.
"""
from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Loyiha ildiz papkasi (olimpiada/)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    """.env dagi 'true/1/yes' qiymatlarini bool ga aylantiradi."""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- Telegram ---
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

    # --- Web ---
    SECRET_KEY: str = os.getenv("SECRET_KEY", "").strip()
    # HTTPS orqali ishlaganda cookie faqat https da yuborilsin
    COOKIE_SECURE: bool = _bool("COOKIE_SECURE", False)
    SESSION_HOURS: int = int(os.getenv("SESSION_HOURS", "12"))

    # --- Birinchi admin (faqat baza bo'sh bo'lsa yaratiladi) ---
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin").strip()
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "").strip()

    # --- Vaqt mintaqasi (barcha vaqtlar shu mintaqada ko'rsatiladi) ---
    TZ_NAME: str = os.getenv("TZ_NAME", "Asia/Tashkent")
    TZ = ZoneInfo(TZ_NAME)

    # --- Papkalar ---
    DATA_DIR: Path = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
    DB_PATH: Path = DATA_DIR / "olimpiada.db"
    UPLOAD_DIR: Path = DATA_DIR / "uploads"
    BACKUP_DIR: Path = DATA_DIR / "backups"
    BACKUP_KEEP: int = int(os.getenv("BACKUP_KEEP", "30"))

    # --- Telegram limitlari: sekundiga yuboriladigan xabarlar soni ---
    BROADCAST_RATE: float = float(os.getenv("BROADCAST_RATE", "20"))

    @classmethod
    def ensure_dirs(cls) -> None:
        """Kerakli papkalarni yaratadi."""
        for d in (cls.DATA_DIR, cls.UPLOAD_DIR, cls.BACKUP_DIR,
                  cls.UPLOAD_DIR / "rules", cls.UPLOAD_DIR / "certificates",
                  cls.UPLOAD_DIR / "broadcasts", cls.UPLOAD_DIR / "certificates" / "assets",
                  cls.UPLOAD_DIR / "fonts", cls.DATA_DIR / "exports"):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.DB_PATH}"


config = Config()

if not config.SECRET_KEY:
    # Kalitsiz sessiyalar xavfsiz emas — ishga tushirishni to'xtatamiz
    raise RuntimeError(
        "SECRET_KEY .env faylda berilmagan. Masalan: "
        "python -c \"import secrets; print(secrets.token_hex(32))\" natijasini qo'ying."
    )

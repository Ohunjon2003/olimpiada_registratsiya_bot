"""Ma'lumotlar bazasi ulanishi (SQLite + SQLAlchemy async).

SQLite bir vaqtda ko'p yozuvni yaxshi ko'tarishi uchun:
  * WAL rejimi — o'qish yozishni to'smaydi;
  * busy_timeout — baza band bo'lsa xato bermasdan kutadi;
  * foreign_keys — bog'liqliklar buzilmasligi uchun.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (AsyncSession, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import DeclarativeBase

from .config import config


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    config.db_url,
    connect_args={"timeout": 30},
    pool_size=10,
    max_overflow=10,
)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """Har bir yangi ulanishda SQLite sozlamalarini yoqamiz."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=15000")
    cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Bot va fon vazifalari uchun sessiya: xato bo'lsa rollback qiladi."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session

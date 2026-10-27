"""SQLite asosidagi FSM xotirasi.

aiogram'ning MemoryStorage'i server qayta ishga tushganda barcha holatlarni
yo'qotadi. Bu xotira esa bazada saqlaydi — o'quvchi ro'yxatdan o'tishni
qayerda to'xtatgan bo'lsa, shu joydan davom etadi.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, DefaultKeyBuilder, StorageKey

from ..db import SessionLocal
from ..models import FsmRecord


class SQLiteStorage(BaseStorage):
    def __init__(self) -> None:
        self.key_builder = DefaultKeyBuilder(with_destiny=True)

    async def _get(self, session, key: StorageKey) -> Optional[FsmRecord]:
        return await session.get(FsmRecord, self.key_builder.build(key))

    async def set_state(self, key: StorageKey, state=None) -> None:
        value = state.state if isinstance(state, State) else state
        async with SessionLocal() as s:
            rec = await self._get(s, key)
            if rec is None:
                if value is None:
                    return
                rec = FsmRecord(key=self.key_builder.build(key), state=value, data="{}")
                s.add(rec)
            else:
                rec.state = value
            await s.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        async with SessionLocal() as s:
            rec = await self._get(s, key)
            return rec.state if rec else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        raw = json.dumps(dict(data), ensure_ascii=False)
        async with SessionLocal() as s:
            rec = await self._get(s, key)
            if rec is None:
                rec = FsmRecord(key=self.key_builder.build(key), state=None, data=raw)
                s.add(rec)
            else:
                rec.data = raw
            await s.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with SessionLocal() as s:
            rec = await self._get(s, key)
            if rec is None or not rec.data:
                return {}
            try:
                return json.loads(rec.data)
            except json.JSONDecodeError:
                return {}

    async def close(self) -> None:
        pass

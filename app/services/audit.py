"""Jurnal (audit log): kim, qachon, nimani o'zgartirgani."""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditLog, User


def _dump(data: Any) -> str:
    if data in (None, "", {}):
        return ""
    return json.dumps(data, ensure_ascii=False, default=str)


def log(session: AsyncSession, user: Optional[User], action: str, entity: str,
        entity_id: Any = "", summary: str = "", old: Any = None, new: Any = None,
        ip: str = "") -> None:
    """Yozuv qo'shadi (commit chaqiruvchi tomonda qilinadi)."""
    session.add(AuditLog(
        user_id=user.id if user else None,
        username=user.username if user else "bot",
        action=action, entity=entity, entity_id=str(entity_id or ""),
        summary=summary[:500], old=_dump(old), new=_dump(new), ip=ip[:64],
    ))

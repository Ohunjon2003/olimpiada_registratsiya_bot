"""Web qismining umumiy vositalari: shablonlar, autentifikatsiya, CSRF, flash xabarlar."""
from __future__ import annotations

import hmac
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models import User
from ..security import new_token
from ..services import settings as st
from ..utils import fmt_dt, fmt_num, format_phone, to_local_input

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
templates.env.filters["dt"] = fmt_dt
templates.env.filters["num"] = fmt_num
templates.env.filters["phone"] = format_phone
templates.env.filters["dtinput"] = to_local_input
templates.env.globals["setting"] = st.get


def _setting_json(key: str):
    """Erkin JSON sozlama (masalan, custom_fonts) — shablonlarda ro'yxat sifatida."""
    import json
    try:
        return json.loads(st.raw(key) or "null") or []
    except ValueError:
        return []


templates.env.globals["setting_json"] = _setting_json


# ---------------------------------------------------------------------------
# Autentifikatsiya
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UserView:
    """Shablonlarda ko'rsatish uchun foydalanuvchi ma'lumoti (ORM'ga bog'lanmagan)."""
    id: int
    username: str
    full_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class LoginRequired(Exception):
    """Login qilinmagan — /login ga yo'naltiriladi (main.py da ushlanadi)."""


def _pw_version(user: User) -> str:
    # Parol o'zgarsa eski sessiyalar avtomatik bekor bo'ladi
    return user.password_hash[-16:]


def login_user(request: Request, user: User) -> None:
    request.session.clear()
    request.session["uid"] = user.id
    request.session["pwv"] = _pw_version(user)
    request.session["csrf"] = new_token()


async def current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User:
    uid = request.session.get("uid")
    if not uid:
        raise LoginRequired()
    user = await session.get(User, uid)
    if user is None or not user.is_active or request.session.get("pwv") != _pw_version(user):
        request.session.clear()
        raise LoginRequired()
    # Shablonlar uchun oddiy nusxa: sessiya rollback qilinganda ham ishlaydi
    request.state.user = UserView(user.id, user.username, user.full_name, user.role)
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "Bu sahifa faqat admin uchun")
    return user


# ---------------------------------------------------------------------------
# CSRF himoyasi: har bir POST so'rovida sessiyadagi token tekshiriladi
# ---------------------------------------------------------------------------
def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = new_token()
        request.session["csrf"] = tok
    return tok


async def verify_csrf(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    expected = request.session.get("csrf", "")
    sent = request.headers.get("x-csrf-token", "")
    if not sent:
        ctype = request.headers.get("content-type", "")
        if "form" in ctype:
            form = await request.form()
            sent = str(form.get("csrf", ""))
    if not expected or not sent or not hmac.compare_digest(expected, sent):
        raise HTTPException(400, "Sahifa eskirgan (CSRF). Sahifani yangilab qayta urinib ko'ring.")


# ---------------------------------------------------------------------------
# Flash xabarlar va render
# ---------------------------------------------------------------------------
def flash(request: Request, message: str, kind: str = "success") -> None:
    # Ro'yxatni qayta tayinlaymiz: Starlette sessiya ichidagi ro'yxat o'zgarishini sezmaydi
    # (aks holda oldingi o'qilmagan xabar bo'lsa, yangisi cookie'ga yozilmay yo'qoladi)
    request.session["flash"] = list(request.session.get("flash", [])) + [[kind, message]]


def pop_flashes(request: Request) -> list:
    return request.session.pop("flash", [])


def render(request: Request, name: str, ctx: Optional[dict] = None, status_code: int = 200):
    ctx = dict(ctx or {})
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx["csrf"] = csrf_token(request)
    ctx["flashes"] = pop_flashes(request)
    ctx["path"] = request.url.path
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def client_ip(request: Request) -> str:
    # Nginx orqali ishlaganda haqiqiy IP X-Forwarded-For'da keladi (uvicorn --proxy-headers)
    return request.client.host if request.client else ""


# ---------------------------------------------------------------------------
# Sahifalash
# ---------------------------------------------------------------------------
class Page:
    def __init__(self, total: int, page: int, per_page: int, params: dict[str, Any]):
        self.total, self.per_page = total, per_page
        self.pages = max(1, math.ceil(total / per_page))
        self.page = min(max(1, page), self.pages)
        self.offset = (self.page - 1) * per_page
        self._params = {k: v for k, v in params.items() if v not in (None, "") and k != "page"}

    def url(self, page: int) -> str:
        return "?" + urlencode({**self._params, "page": page})

    @property
    def window(self) -> list[int]:
        lo, hi = max(1, self.page - 3), min(self.pages, self.page + 3)
        return list(range(lo, hi + 1))


def int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None

"""Kirish, chiqish va parolni almashtirish."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import User
from ...security import (hash_password, login_limiter, password_problem,
                         verify_password)
from ...services import audit
from ...utils import now_utc
from ..core import (client_ip, current_user, flash, login_user, render,
                    verify_csrf)

router = APIRouter(dependencies=[Depends(verify_csrf)])


@router.get("/")
async def root(request: Request, session: AsyncSession = Depends(get_session)):
    uid = request.session.get("uid")
    user = await session.get(User, uid) if uid else None
    if user is None or not user.is_active:
        request.session.clear()  # aks holda /login ↔ / aylanib qolishi mumkin
        return RedirectResponse("/login", 303)
    return RedirectResponse("/admin" if user.is_admin else "/judge", 303)


_bot_check: dict = {"at": 0.0, "ok": True, "error": ""}


@router.get("/health")
async def health(request: Request, session: AsyncSession = Depends(get_session)):
    """Monitoring (UptimeRobot) uchun: baza va bot ishlayaptimi. Muammo bo'lsa — 503."""
    import time
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from ...bot import loader

    out = {"db": True, "bot": None}
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        out["db"] = False
    if loader.bot is not None:
        task = getattr(request.app.state, "bot_task", None)
        alive = task is None or not task.done()  # task yo'q — test muhiti
        # Telegram'ga ulanishni ko'pi bilan minutiga bir marta tekshiramiz
        if time.monotonic() - _bot_check["at"] > 60:
            _bot_check["at"] = time.monotonic()
            try:
                await loader.bot.get_me()
                _bot_check.update(ok=True, error="")
            except Exception as e:
                _bot_check.update(ok=False, error=type(e).__name__)
        out["bot"] = alive and _bot_check["ok"]
        if _bot_check["error"]:
            out["bot_error"] = _bot_check["error"]
    out["ok"] = out["db"] and out["bot"] is not False
    return JSONResponse(out, status_code=200 if out["ok"] else 503)


@router.get("/login")
async def login_page(request: Request):
    if request.session.get("uid"):
        return RedirectResponse("/", 303)
    return render(request, "login.html", {"user": None})


@router.post("/login")
async def login(request: Request, session: AsyncSession = Depends(get_session)):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    ip = client_ip(request)
    if login_limiter.blocked(ip):
        return render(request, "login.html", {"user": None, "username": username,
                                             "error": "Juda ko'p noto'g'ri urinish. 10 daqiqadan so'ng qayta urinib ko'ring."}, 429)
    user = (await session.execute(select(User).where(User.username == username))).scalars().first()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        login_limiter.fail(ip)
        return render(request, "login.html", {"user": None, "username": username,
                                             "error": "Login yoki parol noto'g'ri"}, 401)
    login_limiter.reset(ip)
    user.last_login = now_utc()
    audit.log(session, user, "login", "user", user.id, "Tizimga kirdi", ip=ip)
    await session.commit()
    login_user(request, user)
    return RedirectResponse("/admin" if user.is_admin else "/judge", 303)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/account")
async def account(request: Request, user: User = Depends(current_user)):
    return render(request, "account.html")


@router.post("/account")
async def account_save(request: Request, user: User = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    form = await request.form()
    old, new, new2 = (str(form.get(k, "")) for k in ("old", "new", "new2"))
    user = await session.get(User, user.id)
    error = None
    if not verify_password(old, user.password_hash):
        error = "Joriy parol noto'g'ri"
    elif new != new2:
        error = "Yangi parollar bir xil emas"
    else:
        error = password_problem(new)
    if error:
        return render(request, "account.html", {"error": error}, 400)
    user.password_hash = hash_password(new)
    audit.log(session, user, "password", "user", user.id, "Parolini almashtirdi", ip=client_ip(request))
    await session.commit()
    login_user(request, user)  # yangi parol bilan sessiyani yangilaymiz
    flash(request, "Parol almashtirildi")
    return RedirectResponse("/account", 303)

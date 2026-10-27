"""Admin: Telegram xabarnomalar — akkaunt ulash, sozlamalar, sinov."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...models import User
from ...services import audit, backup, notify
from ...services import settings as st
from ..core import client_ip, flash, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/notify", dependencies=[Depends(verify_csrf)])


async def _bot_username() -> str:
    from ...bot import loader
    if loader.bot is None:
        return ""
    try:
        return (await loader.bot.me()).username or ""
    except Exception:
        return ""


@router.get("")
async def notify_page(request: Request, user: User = Depends(require_admin)):
    code = request.session.pop("link_code", None)
    return render(request, "admin/notify.html", {
        "chats": notify.admin_chats(), "code": code, "bot_username": await _bot_username(),
        "ttl_min": notify.CODE_TTL // 60,
    })


@router.post("/code")
async def notify_code(request: Request, user: User = Depends(require_admin)):
    """Bir martalik ulash kodi."""
    request.session["link_code"] = notify.new_code(user.username)
    return RedirectResponse("/admin/notify#link", 303)


@router.post("/settings")
async def notify_settings(request: Request, user: User = Depends(require_admin),
                          session: AsyncSession = Depends(get_session)):
    form = await request.form()
    try:
        every = max(0, int(form.get("notify_every") or 0))
    except ValueError:
        every = 0
    values = {"notify_every": every,
              "notify_errors": form.get("notify_errors") == "on",
              "backup_to_telegram": form.get("backup_to_telegram") == "on"}
    await st.set_many(session, values)
    audit.log(session, user, "update", "settings", "", "Telegram xabarnomalar", new=values, ip=client_ip(request))
    await session.commit()
    flash(request, "Saqlandi")
    return RedirectResponse("/admin/notify", 303)


@router.post("/remove/{chat_id}")
async def notify_remove(chat_id: int, request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    await notify.remove_chat(session, chat_id)
    audit.log(session, user, "delete", "admin_chat", chat_id, "Telegram akkaunt uzildi", ip=client_ip(request))
    await session.commit()
    flash(request, "Akkaunt uzildi")
    return RedirectResponse("/admin/notify", 303)


@router.post("/test")
async def notify_test(request: Request, user: User = Depends(require_admin)):
    n = await notify.send(f"🔔 Sinov xabari. Xabarnomalar ishlayapti! ✅\n(yuborgan: {user.username})")
    flash(request, f"Sinov xabari {n} ta akkauntga yuborildi" if n
          else "Yuborilmadi: akkaunt ulanmagan yoki bot ishlamayapti", "success" if n else "error")
    return RedirectResponse("/admin/notify", 303)


@router.post("/backup")
async def notify_backup_now(request: Request, user: User = Depends(require_admin),
                            session: AsyncSession = Depends(get_session)):
    """Hozir zaxira olib, darhol Telegram'ga yuborish."""
    path = await asyncio.to_thread(backup.make_backup, "manual")
    audit.log(session, user, "create", "backup", "", path.name, ip=client_ip(request))
    await session.commit()
    n = await notify.send_backup(path, f"💾 Zaxira nusxa: {path.name} (yuborgan: {user.username})")
    flash(request, f"Zaxira olindi va {n} ta akkauntga yuborildi" if n
          else "Zaxira olindi, lekin Telegram'ga yuborilmadi (akkaunt ulanmagan yoki bot ishlamayapti)",
          "success" if n else "error")
    return RedirectResponse("/admin/notify", 303)

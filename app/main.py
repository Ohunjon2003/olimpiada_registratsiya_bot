"""Ilovaning kirish nuqtasi: web panel (FastAPI) va Telegram bot bitta jarayonda.

Ishga tushirish:  uvicorn app.main:app --host 127.0.0.1 --port 8000
DIQQAT: faqat BITTA worker bilan ishga tushiring (bot polling bitta bo'lishi shart).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from .bootstrap import run_migrations, seed
from .config import config
from .db import engine
from .web.core import LoginRequired, render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
log = logging.getLogger("app")


async def _run_bot(bot, dp) -> None:
    """Botni ishga tushiradi. Telegram vaqtincha ishlamasa — qayta-qayta urinadi."""
    from aiogram.exceptions import TelegramUnauthorizedError
    from aiogram.types import BotCommand
    delay = 5
    while True:
        try:
            me = await bot.get_me()
            await bot.delete_webhook(drop_pending_updates=False)
            await bot.set_my_commands([BotCommand(command="start", description="Boshlash / Бошлаш"),
                                       BotCommand(command="lang", description="Til / Тил")])
            log.info("Bot ulandi: @%s", me.username)
            await dp.start_polling(bot, handle_signals=False, allowed_updates=dp.resolve_used_update_types())
            return  # stop_polling() chaqirildi — normal to'xtash
        except asyncio.CancelledError:
            raise
        except TelegramUnauthorizedError:
            log.error("BOT_TOKEN noto'g'ri (Telegram rad etdi). .env faylni tekshiring.")
        except Exception as e:
            log.error("Bot Telegramga ulana olmadi: %s", e)
        log.info("Bot %s soniyadan keyin qayta ulanadi", delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1) Baza tuzilmasi va boshlang'ich ma'lumotlar
    await asyncio.to_thread(run_migrations)
    await seed()

    # 2) Bot va fon vazifalari
    from .bot import loader
    from .services import backup, broadcast
    bot, dp = loader.create()
    tasks = [asyncio.create_task(backup.backup_loop(), name="backup")]
    if bot is not None:
        bot_task = asyncio.create_task(_run_bot(bot, dp), name="bot")
        app.state.bot_task = bot_task  # /health tekshiruvi uchun
        tasks.append(bot_task)
        tasks.append(asyncio.create_task(broadcast.scheduler_loop(), name="broadcast"))
    log.info("Ilova ishga tushdi%s", "" if bot else " (botsiz)")
    try:
        yield
    finally:
        if bot is not None:
            with suppress(Exception):
                await dp.stop_polling()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await t
        if bot is not None:
            with suppress(Exception):
                await bot.session.close()
        await engine.dispose()


app = FastAPI(title="Olimpiada", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie="olimp_session",
                   max_age=config.SESSION_HOURS * 3600, same_site="lax", https_only=config.COOKIE_SECURE)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


@app.exception_handler(LoginRequired)
async def login_required(request: Request, exc: LoginRequired):
    if request.headers.get("hx-request"):
        # HTMX so'rovi: butun sahifani login'ga yo'naltiramiz
        return PlainTextResponse("Sessiya tugagan", status_code=401, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", 303)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Xatolik"
    if request.headers.get("hx-request") or request.url.path.startswith("/static"):
        return PlainTextResponse(detail, status_code=exc.status_code)
    titles = {403: "Ruxsat yo'q", 404: "Topilmadi", 400: "Noto'g'ri so'rov", 429: "Juda ko'p so'rov"}
    if exc.status_code == 404 and detail == "Not Found":
        detail = "Bunday sahifa mavjud emas."
    return render(request, "error.html", {"title": titles.get(exc.status_code, "Xatolik"), "message": detail},
                  exc.status_code)


@app.exception_handler(Exception)
async def server_error(request: Request, exc: Exception):
    log.exception("Server xatosi: %s %s", request.method, request.url.path)
    from .services import notify
    await notify.error("Web panelda xato", f"{request.method} {request.url.path}\n{type(exc).__name__}: {exc}",
                       key="web_error")
    if request.headers.get("hx-request"):
        return PlainTextResponse("Serverda xatolik yuz berdi", status_code=500)
    return render(request, "error.html", {"title": "Serverda xatolik",
                                         "message": "Kutilmagan xatolik yuz berdi. Jurnal (log) ga yozildi."}, 500)


app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")

from .web.routers import (admin, appeals, archive, attendance, auth, broadcasts, certificates, crud,  # noqa: E402
                          judge, notify, participants, ranking, texts)

for r in (auth.router, admin.router, crud.router, participants.router, judge.router,
          ranking.router, broadcasts.router, certificates.router, texts.router,
          notify.router, appeals.router, attendance.router,
          archive.router):
    app.include_router(r)


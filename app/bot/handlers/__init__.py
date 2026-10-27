"""Bot handlerlarini ulash (tartib muhim!)."""
import logging

from aiogram import Dispatcher
from aiogram.types import ErrorEvent

from ..middlewares import PerUserLock
from ..texts import t
from . import admin, appeal, menu, registration

log = logging.getLogger("bot")


def setup_routers(dp: Dispatcher) -> None:
    dp.update.outer_middleware(PerUserLock())
    # 1) /start va til  2) ro'yxatdan o'tish bosqichlari  3) menyu va qolganlar
    dp.include_router(admin.router)   # /link, /stat — adminlar uchun
    dp.include_router(menu.router)
    dp.include_router(registration.router)
    dp.include_router(appeal.router)  # e'tiroz (menyu tugmasidan oldin)
    dp.include_router(menu.tail)

    @dp.errors()
    async def on_error(event: ErrorEvent) -> bool:
        log.exception("Bot xatosi: %s", event.exception)
        from ...services import notify
        await notify.error("Botda xato", f"{type(event.exception).__name__}: {event.exception}", key="bot_error")
        upd = event.update
        try:
            if upd.message:
                await upd.message.answer(t("error"))
            elif upd.callback_query:
                await upd.callback_query.answer(t("error")[:190], show_alert=True)
        except Exception:
            pass
        return True

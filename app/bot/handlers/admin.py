"""Admin buyruqlari botda: /link (akkauntni ulash), /stat, /unlink.

Bu router eng birinchi ulanadi. /stat va /unlink faqat ulangan adminlarga javob beradi —
boshqalar uchun bu buyruqlar oddiy matn kabi keyingi handlerlarga o'tadi.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, Filter
from aiogram.types import BotCommand, BotCommandScopeChat, Message

from ...db import session_scope
from ...services import audit, notify, stats

log = logging.getLogger("bot.admin")
router = Router(name="admin")


class IsAdminChat(Filter):
    async def __call__(self, message: Message) -> bool:
        return notify.is_admin_chat(message.chat.id)


ADMIN_HELP = ("🛠 <b>Admin buyruqlari</b>\n"
              "/stat — statistika\n"
              "/unlink — bu akkauntni xabarnomalardan uzish\n\n"
              "Bot sizga yuboradi: har N-o'quvchi haqida xabar, xatolar, zaxira nusxalar.")


@router.message(CommandStart(deep_link=True, magic=F.args.regexp(r"^link_\d{6}$")), F.chat.type == "private")
@router.message(Command("link"), F.chat.type == "private")
async def link(msg: Message, command: CommandObject) -> None:
    # Panel havolasi orqali kelsa: /start link_123456; qo'lda yozilsa: /link 123456
    code = (command.args or "").strip().removeprefix("link_")
    by = notify.consume_code(code) if code else None
    if by is None:
        await msg.answer("❌ Kod noto'g'ri yoki muddati o'tgan. Paneldagi «🔔 Telegram xabarnomalar» "
                         "sahifasidan yangi kod oling.")
        return
    name = " ".join(x for x in (msg.from_user.first_name, msg.from_user.last_name) if x) or str(msg.chat.id)
    if msg.from_user.username:
        name += f" (@{msg.from_user.username})"
    async with session_scope() as s:
        await notify.add_chat(s, msg.chat.id, name, by)
        audit.log(s, None, "create", "admin_chat", msg.chat.id, f"Telegram ulandi: {name} (kod: {by})")
        await s.commit()
    log.info("Admin Telegram ulandi: %s (%s)", name, msg.chat.id)
    try:  # admin uchun buyruqlar menyusi (faqat shu chatda ko'rinadi)
        await msg.bot.set_my_commands([BotCommand(command="stat", description="Statistika"),
                                       BotCommand(command="admin", description="Admin buyruqlari"),
                                       BotCommand(command="unlink", description="Xabarnomalarni o'chirish"),
                                       BotCommand(command="start", description="Boshlash")],
                                      scope=BotCommandScopeChat(chat_id=msg.chat.id))
    except Exception as e:
        log.warning("Admin buyruqlari menyusi o'rnatilmadi: %s", e)
    await msg.answer("✅ Akkauntingiz admin sifatida ulandi!\n\n" + ADMIN_HELP)


@router.message(Command("stat"), IsAdminChat())
async def stat(msg: Message) -> None:
    async with session_scope() as s:
        await msg.answer(await stats.stat_text(s))


@router.message(Command("admin"), IsAdminChat())
async def admin_help(msg: Message) -> None:
    await msg.answer(ADMIN_HELP)


@router.message(Command("unlink"), IsAdminChat())
async def unlink(msg: Message) -> None:
    async with session_scope() as s:
        await notify.remove_chat(s, msg.chat.id)
        audit.log(s, None, "delete", "admin_chat", msg.chat.id, "Telegram uzildi (botdan)")
        await s.commit()
    try:
        await msg.bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=msg.chat.id))
    except Exception:
        pass
    await msg.answer("👋 Akkaunt uzildi. Endi xabarnomalar kelmaydi.")

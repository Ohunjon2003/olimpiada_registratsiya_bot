"""Buyruq qatori vositalari.

  python -m app.cli create-admin LOGIN       — yangi admin yaratish (parol so'raladi)
  python -m app.cli reset-password LOGIN     — parolni tiklash
  python -m app.cli backup                   — zaxira nusxa olish
"""
from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy import select

from .bootstrap import run_migrations
from .db import engine, session_scope
from .models import User
from .security import hash_password, password_problem
from .services.backup import make_backup


def _ask_password() -> str:
    while True:
        pw = getpass.getpass("Parol: ")
        problem = password_problem(pw)
        if problem:
            print("❌", problem)
            continue
        if pw != getpass.getpass("Parol (takror): "):
            print("❌ Parollar bir xil emas")
            continue
        return pw


async def _create_admin(username: str) -> None:
    async with session_scope() as s:
        if (await s.execute(select(User).where(User.username == username))).scalars().first():
            print(f"❌ «{username}» allaqachon mavjud. reset-password dan foydalaning.")
            return
        s.add(User(username=username, role="admin", full_name="Administrator",
                   password_hash=hash_password(_ask_password())))
        await s.commit()
    print(f"✅ Admin yaratildi: {username}")


async def _reset(username: str) -> None:
    async with session_scope() as s:
        u = (await s.execute(select(User).where(User.username == username))).scalars().first()
        if not u:
            print(f"❌ «{username}» topilmadi")
            return
        u.password_hash = hash_password(_ask_password())
        u.is_active = True
        await s.commit()
    print(f"✅ «{username}» paroli yangilandi")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    run_migrations()
    if cmd == "backup":
        print("✅", make_backup("manual"))
        return
    if len(sys.argv) < 3:
        print(__doc__)
        return
    coro = {"create-admin": _create_admin, "reset-password": _reset}.get(cmd)
    if not coro:
        print(__doc__)
        return

    async def run():
        await coro(sys.argv[2].strip().lower())
        await engine.dispose()
    asyncio.run(run())


if __name__ == "__main__":
    main()

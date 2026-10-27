"""Ishga tushirishdagi tayyorgarlik: migratsiya va boshlang'ich ma'lumotlar."""
from __future__ import annotations

import json
import logging

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import func, select

from .config import BASE_DIR, config
from .db import session_scope
from .models import CertificateTemplate, Grade, Round, Setting, User
from .security import hash_password
from .services import certificates as certs
from .services import settings as st

log = logging.getLogger("bootstrap")


def run_migrations() -> None:
    """Baza tuzilmasini oxirgi versiyagacha yangilaydi (alembic upgrade head)."""
    config.ensure_dirs()
    cfg = AlembicConfig(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
    command.upgrade(cfg, "head")


async def seed() -> None:
    """Birinchi ishga tushishda kerakli boshlang'ich ma'lumotlarni qo'shadi."""
    async with session_scope() as s:
        # 1) Birinchi admin
        admins = (await s.execute(select(func.count()).select_from(User)
                                  .where(User.role == "admin"))).scalar_one()
        if admins == 0:
            if not config.ADMIN_PASSWORD:
                log.warning("Admin yo'q va ADMIN_PASSWORD berilmagan. "
                            "'python -m app.cli create-admin' bilan admin yarating.")
            else:
                s.add(User(username=config.ADMIN_USERNAME, role="admin", full_name="Administrator",
                           password_hash=hash_password(config.ADMIN_PASSWORD)))
                log.info("Admin yaratildi: %s", config.ADMIN_USERNAME)

        seeded = await s.get(Setting, "_seeded")
        if seeded is None:
            # 2) Sinflar 5–11 (admin keyin o'zgartirishi mumkin)
            if (await s.execute(select(func.count()).select_from(Grade))).scalar_one() == 0:
                for i, g in enumerate(range(5, 12)):
                    s.add(Grade(name=str(g), position=i))
            # 3) Birinchi tur
            if (await s.execute(select(func.count()).select_from(Round))).scalar_one() == 0:
                s.add(Round(name="1-tur", position=0, is_current=True, open_to_all=True))
            # 4) Standart sertifikat shabloni (o'chiq holatda)
            if (await s.execute(select(func.count()).select_from(CertificateTemplate))).scalar_one() == 0:
                w, h = certs.image_size(None)
                s.add(CertificateTemplate(name="G'oliblar diplomi (1–3 o'rin)", is_active=False,
                                          width=w, height=h, rule="top", top_n=3,
                                          fields_json=json.dumps(certs.DEFAULT_FIELDS, ensure_ascii=False)))
            s.add(Setting(key="_seeded", value="1"))
        # 5) Eski standart matnlarni (admin o'zgartirmagan bo'lsa) yangi, emojili variantga almashtirish
        for key, olds in st.OLD_DEFAULTS.items():
            row = await s.get(Setting, key)
            if row is not None and row.value.replace("\r\n", "\n").strip() in [o.strip() for o in olds]:
                row.value = st.DEFAULTS[key][1]
        await s.commit()
        await st.load(s)

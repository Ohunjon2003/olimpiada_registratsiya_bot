"""Alembic muhiti: sinxron SQLite ulanishi orqali migratsiyalarni bajaradi."""
from alembic import context
from sqlalchemy import create_engine

from app.config import config
from app.db import Base
from app import models  # noqa: F401 — jadvallar metadata'ga yozilishi uchun

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=f"sqlite:///{config.DB_PATH}", target_metadata=target_metadata,
                      literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.ensure_dirs()
    engine = create_engine(f"sqlite:///{config.DB_PATH}")
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata,
                          render_as_batch=True)  # SQLite ALTER cheklovlari uchun
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

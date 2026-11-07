"""natijalar faqat admin yuborganda

Natijalar endi FAQAT «📨 Natijalarni e'lon qilish va botga yuborish» tugmasi bilan ochiladi.
Yangilanishda e'lon holati bir marta yopiladi (ball kiritish/skaner paytida botda hech narsa ko'rinmasin).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07 20:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text("UPDATE settings SET value = '0' WHERE key = 'results_published'"))


def downgrade() -> None:
    pass

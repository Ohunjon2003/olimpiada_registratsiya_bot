"""voucherlar

Qatnashganlarga beriladigan pul qiymatli voucherlar (raqam, QR orqali tekshirish, ishlatilganini belgilash).

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06 13:10:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'vouchers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('participant_id', sa.Integer(), nullable=False),
        sa.Column('round_id', sa.Integer(), nullable=False),
        sa.Column('serial', sa.String(length=32), nullable=False),
        sa.Column('amount', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('issued_by', sa.String(length=64), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('redeemed_at', sa.DateTime(), nullable=True),
        sa.Column('redeemed_by', sa.String(length=64), nullable=False),
        sa.Column('redeem_note', sa.String(length=255), nullable=False),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('cancel_reason', sa.String(length=255), nullable=False),
        sa.ForeignKeyConstraint(['participant_id'], ['participants.id'], name='fk_vouchers_participant_id_participants', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], name='fk_vouchers_round_id_rounds', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name='pk_vouchers'),
        sa.UniqueConstraint('participant_id', 'round_id', name='uq_voucher_participant_round'),
        sa.UniqueConstraint('serial', name='uq_vouchers_serial'),
    )


def downgrade() -> None:
    op.drop_table('vouchers')

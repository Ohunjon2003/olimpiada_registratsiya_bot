"""avtomatik tekshiruv

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 15:59:13.901284
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Avtomatik tekshiruv: kalitlar, skanerlar, fan sozlamalari
    op.create_table('answer_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('variant', sa.Integer(), nullable=False),
    sa.Column('answers', sa.Text(), nullable=False),
    sa.Column('updated_by', sa.String(length=64), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('subject_id', 'round_id', 'variant', name='uq_answer_key')
    )
    op.create_table('sheet_scans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('participant_id', sa.Integer(), nullable=False),
    sa.Column('qr_participant_id', sa.Integer(), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('variant', sa.Integer(), nullable=False),
    sa.Column('answers', sa.String(length=400), nullable=False),
    sa.Column('id_marked', sa.String(length=12), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=False),
    sa.Column('correct', sa.Integer(), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('percent', sa.Float(), nullable=True),
    sa.Column('image', sa.String(length=128), nullable=False),
    sa.Column('raw_image', sa.String(length=128), nullable=False),
    sa.Column('scanned_by', sa.String(length=64), nullable=False),
    sa.Column('scanned_at', sa.DateTime(), nullable=False),
    sa.Column('decided_by', sa.String(length=64), nullable=False),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['participant_id'], ['participants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['subject_id'], ['subjects.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sheet_scans', schema=None) as batch_op:
        batch_op.create_index('ix_scan_lookup', ['subject_id', 'round_id', 'participant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sheet_scans_status'), ['status'], unique=False)

    with op.batch_alter_table('subjects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('omr_enabled', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('variants', sa.Integer(), server_default='1', nullable=False))



def downgrade() -> None:
    # Avtomatik tekshiruv: kalitlar, skanerlar, fan sozlamalari
    with op.batch_alter_table('subjects', schema=None) as batch_op:
        batch_op.drop_column('variants')
        batch_op.drop_column('omr_enabled')

    with op.batch_alter_table('sheet_scans', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sheet_scans_status'))
        batch_op.drop_index('ix_scan_lookup')

    op.drop_table('sheet_scans')
    op.drop_table('answer_keys')

"""joylashtirish va dizayn

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01 14:42:50.813021
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bino, xona, smena, o'rindiqlar va yangi dizayn ustunlari
    op.create_table('buildings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('name_cyr', sa.String(length=128), nullable=False),
    sa.Column('address', sa.String(length=256), nullable=False),
    sa.Column('address_cyr', sa.String(length=256), nullable=False),
    sa.Column('map_url', sa.String(length=512), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('exam_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('name_cyr', sa.String(length=64), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('arrive_time', sa.String(length=5), nullable=False),
    sa.Column('start_time', sa.String(length=5), nullable=False),
    sa.Column('duration_min', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('rooms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('building_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('floor', sa.String(length=16), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('desks', sa.Integer(), nullable=False),
    sa.Column('seats_per_desk', sa.Integer(), nullable=False),
    sa.Column('disabled_json', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['building_id'], ['buildings.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('seat_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('participant_id', sa.Integer(), nullable=False),
    sa.Column('round_id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=False),
    sa.Column('room_id', sa.Integer(), nullable=False),
    sa.Column('row', sa.Integer(), nullable=False),
    sa.Column('desk', sa.Integer(), nullable=False),
    sa.Column('seat', sa.Integer(), nullable=False),
    sa.Column('pass_sent_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['participant_id'], ['participants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['room_id'], ['rooms.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['round_id'], ['rounds.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['exam_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('participant_id', 'round_id', name='uq_seat_participant_round'),
    sa.UniqueConstraint('session_id', 'room_id', 'row', 'desk', 'seat', name='uq_seat_place')
    )
    with op.batch_alter_table('seat_assignments', schema=None) as batch_op:
        batch_op.create_index('ix_seat_round_session_room', ['round_id', 'session_id', 'room_id'], unique=False)

    with op.batch_alter_table('certificate_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sa.String(length=16), server_default='certificate', nullable=False))
        batch_op.add_column(sa.Column('design_json', sa.Text(), server_default='', nullable=False))



def downgrade() -> None:
    # Bino, xona, smena, o'rindiqlar va yangi dizayn ustunlari
    with op.batch_alter_table('certificate_templates', schema=None) as batch_op:
        batch_op.drop_column('design_json')
        batch_op.drop_column('kind')

    with op.batch_alter_table('seat_assignments', schema=None) as batch_op:
        batch_op.drop_index('ix_seat_round_session_room')

    op.drop_table('seat_assignments')
    op.drop_table('rooms')
    op.drop_table('exam_sessions')
    op.drop_table('buildings')

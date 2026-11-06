"""sinf smenalari

Smena endi bitta sinfga tegishli bo'lishi mumkin (grade_id) va o'quvchilar soniga chek
qo'yiladi (max_students). Eski smenalar o'zgarmaydi: ikkala maydon bo'sh — umumiy smena.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-06 09:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('exam_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('grade_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('max_students', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_exam_sessions_grade_id_grades', 'grades', ['grade_id'], ['id'],
                                    ondelete='SET NULL')


def downgrade() -> None:
    with op.batch_alter_table('exam_sessions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_exam_sessions_grade_id_grades', type_='foreignkey')
        batch_op.drop_column('max_students')
        batch_op.drop_column('grade_id')

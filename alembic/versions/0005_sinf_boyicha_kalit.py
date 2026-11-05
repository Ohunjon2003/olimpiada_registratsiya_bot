"""sinf bo'yicha kalit

Har sinfning savollari har xil — kalit endi fan × tur × SINF × variant.
Eski (umumiy) kalitlar shu fandagi har bir sinfga nusxalanadi, shunda hech narsa yo'qolmaydi.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05 09:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('answer_keys', schema=None) as batch_op:
        batch_op.add_column(sa.Column('grade_id', sa.Integer(), nullable=True))
        batch_op.drop_constraint('uq_answer_key', type_='unique')
        batch_op.create_unique_constraint('uq_answer_key_grade', ['subject_id', 'round_id', 'grade_id', 'variant'])
        batch_op.create_foreign_key('fk_answer_keys_grade_id_grades', 'grades', ['grade_id'], ['id'], ondelete='CASCADE')
    with op.batch_alter_table('sheet_scans', schema=None) as batch_op:
        batch_op.add_column(sa.Column('grade_id', sa.Integer(), nullable=True))

    conn = op.get_bind()
    # 1) Eski skanerlar: sinf — o'quvchining sinfi
    conn.execute(sa.text(
        "UPDATE sheet_scans SET grade_id = (SELECT p.grade_id FROM participants p WHERE p.id = sheet_scans.participant_id)"))
    # 2) Umumiy kalitlarni har sinfga nusxalash
    keys = conn.execute(sa.text(
        "SELECT id, subject_id, round_id, variant, answers, updated_by, updated_at FROM answer_keys WHERE grade_id IS NULL")).fetchall()
    for k in keys:
        grades = [r[0] for r in conn.execute(sa.text(
            "SELECT grade_id FROM subject_grades WHERE subject_id = :s"), {"s": k.subject_id})]
        if not grades:   # fan barcha sinflar uchun — shu fanni tanlaganlar sinflari
            grades = [r[0] for r in conn.execute(sa.text(
                "SELECT DISTINCT p.grade_id FROM participants p JOIN participant_subjects ps ON ps.participant_id = p.id "
                "WHERE ps.subject_id = :s"), {"s": k.subject_id})]
        if not grades:
            continue      # hech kim yo'q — umumiy kalit qoladi (ishlatilmaydi)
        for g in grades:
            exists = conn.execute(sa.text(
                "SELECT 1 FROM answer_keys WHERE subject_id=:s AND round_id=:r AND grade_id=:g AND variant=:v"),
                {"s": k.subject_id, "r": k.round_id, "g": g, "v": k.variant}).first()
            if not exists:
                conn.execute(sa.text(
                    "INSERT INTO answer_keys (subject_id, round_id, grade_id, variant, answers, updated_by, updated_at) "
                    "VALUES (:s, :r, :g, :v, :a, :u, :t)"),
                    {"s": k.subject_id, "r": k.round_id, "g": g, "v": k.variant, "a": k.answers,
                     "u": k.updated_by, "t": k.updated_at})
        conn.execute(sa.text("DELETE FROM answer_keys WHERE id = :i"), {"i": k.id})


def downgrade() -> None:
    conn = op.get_bind()
    # Har (fan, tur, variant) uchun bittadan kalit qoldiramiz
    conn.execute(sa.text(
        "DELETE FROM answer_keys WHERE id NOT IN (SELECT MIN(id) FROM answer_keys GROUP BY subject_id, round_id, variant)"))
    with op.batch_alter_table('sheet_scans', schema=None) as batch_op:
        batch_op.drop_column('grade_id')
    with op.batch_alter_table('answer_keys', schema=None) as batch_op:
        batch_op.drop_constraint('fk_answer_keys_grade_id_grades', type_='foreignkey')
        batch_op.drop_constraint('uq_answer_key_grade', type_='unique')
        batch_op.create_unique_constraint('uq_answer_key', ['subject_id', 'round_id', 'variant'])
        batch_op.drop_column('grade_id')

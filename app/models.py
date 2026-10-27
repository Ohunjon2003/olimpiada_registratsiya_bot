"""Ma'lumotlar bazasi jadvallari.

Vaqtlar bazada UTC (naive) ko'rinishida saqlanadi, ekranga chiqarishda
Asia/Tashkent vaqtiga o'tkaziladi (app/utils.py).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Float,
                        ForeignKey, Integer, String, Table, Text,
                        UniqueConstraint, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils import now_utc

# ---------------------------------------------------------------------------
# Ko'pga-ko'p (M2M) bog'lovchi jadvallar
# ---------------------------------------------------------------------------
judge_subjects = Table(
    "judge_subjects", Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("subject_id", ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True),
)

subject_grades = Table(
    "subject_grades", Base.metadata,
    Column("subject_id", ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True),
    Column("grade_id", ForeignKey("grades.id", ondelete="CASCADE"), primary_key=True),
)

block_subjects = Table(
    "block_subjects", Base.metadata,
    Column("block_id", ForeignKey("blocks.id", ondelete="CASCADE"), primary_key=True),
    Column("subject_id", ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True),
)

participant_subjects = Table(
    "participant_subjects", Base.metadata,
    Column("participant_id", ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
    Column("subject_id", ForeignKey("subjects.id", ondelete="RESTRICT"), primary_key=True),
)

round_participants = Table(
    "round_participants", Base.metadata,
    Column("round_id", ForeignKey("rounds.id", ondelete="CASCADE"), primary_key=True),
    Column("participant_id", ForeignKey("participants.id", ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------------------
# Foydalanuvchilar (admin va hakamlar)
# ---------------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="judge")  # admin | judge
    full_name: Mapped[str] = mapped_column(String(128), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    subjects: Mapped[list["Subject"]] = relationship(secondary=judge_subjects, lazy="selectin")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class Setting(Base):
    """Kalit-qiymat ko'rinishidagi sozlamalar (admin paneldan o'zgaradi)."""
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# ---------------------------------------------------------------------------
# Ma'lumotnomalar (admin paneldan qo'shiladi)
# ---------------------------------------------------------------------------
class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    title_cyr: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(16), default="telegram")  # telegram | instagram | other
    url: Mapped[str] = mapped_column(String(256))
    chat_id: Mapped[str] = mapped_column(String(64), default="")  # @username yoki -100...
    verify: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Rule(Base):
    """Olimpiada nizomi: matn va/yoki fayl (PDF)."""
    __tablename__ = "rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    title_cyr: Mapped[str] = mapped_column(String(128), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    text_cyr: Mapped[str] = mapped_column(Text, default="")
    file_path: Mapped[str] = mapped_column(String(256), default="")
    file_name: Mapped[str] = mapped_column(String(256), default="")
    tg_file_id: Mapped[str] = mapped_column(String(256), default="")  # Telegram kesh
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class School(Base):
    __tablename__ = "schools"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    name_cyr: Mapped[str] = mapped_column(String(128), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Grade(Base):
    __tablename__ = "grades"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    name_cyr: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Subject(Base):
    """Fan va uning baholash usuli.

    scoring_mode:
      * uniform — barcha savollar bir xil ball: total_questions × points_per_question
      * groups  — savollar guruhlarga bo'lingan, har guruh o'z bali bilan (ScoreGroup)
      * manual  — hakam ballni to'g'ridan-to'g'ri kiritadi (max_score gacha)
    """
    __tablename__ = "subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    name_cyr: Mapped[str] = mapped_column(String(128), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    scoring_mode: Mapped[str] = mapped_column(String(16), default="uniform")
    total_questions: Mapped[int] = mapped_column(Integer, default=30)
    points_per_question: Mapped[float] = mapped_column(Float, default=1.0)
    max_score_manual: Mapped[float] = mapped_column(Float, default=100.0)

    grades: Mapped[list["Grade"]] = relationship(secondary=subject_grades, lazy="selectin")
    groups: Mapped[list["ScoreGroup"]] = relationship(
        back_populates="subject", lazy="selectin", cascade="all, delete-orphan",
        order_by="ScoreGroup.position")

    @property
    def max_score(self) -> float:
        """Fandan olinishi mumkin bo'lgan eng yuqori ball."""
        if self.scoring_mode == "groups":
            return float(sum(g.question_count * g.points for g in self.groups))
        if self.scoring_mode == "manual":
            return float(self.max_score_manual)
        return float(self.total_questions * self.points_per_question)

    @property
    def question_total(self) -> int:
        if self.scoring_mode == "groups":
            return sum(g.question_count for g in self.groups)
        if self.scoring_mode == "manual":
            return 0
        return self.total_questions


class ScoreGroup(Base):
    """'groups' rejimida: masalan 1–10 savol 1 balldan, 11–20 savol 2 balldan."""
    __tablename__ = "score_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    question_count: Mapped[int] = mapped_column(Integer)
    points: Mapped[float] = mapped_column(Float)
    position: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped["Subject"] = relationship(back_populates="groups")


class Block(Base):
    """Bir nechta fan birlashtirilgan blok (blok reytingi uchun)."""
    __tablename__ = "blocks"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    name_cyr: Mapped[str] = mapped_column(String(128), default="")
    # sum — ballar yig'indisi; avg_percent — foizlar o'rtachasi
    aggregation: Mapped[str] = mapped_column(String(16), default="sum")
    # True — faqat blokdagi barcha fanlarni tanlaganlar reytingga kiradi
    require_all: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    subjects: Mapped[list["Subject"]] = relationship(secondary=block_subjects, lazy="selectin")


class Round(Base):
    """Olimpiada turi (1-tur, final ...)."""
    __tablename__ = "rounds"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    name_cyr: Mapped[str] = mapped_column(String(64), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # True — barcha ishtirokchilar; False — faqat keyingi turga o'tkazilganlar
    open_to_all: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CustomQuestion(Base):
    """Admin qo'shadigan qo'shimcha savollar."""
    __tablename__ = "custom_questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    text_cyr: Mapped[str] = mapped_column(Text, default="")
    example: Mapped[str] = mapped_column(String(256), default="")
    example_cyr: Mapped[str] = mapped_column(String(256), default="")
    # text | number | choice | multichoice | phone
    qtype: Mapped[str] = mapped_column(String(16), default="text")
    options: Mapped[str] = mapped_column(Text, default="")  # har qatorda bitta variant
    options_cyr: Mapped[str] = mapped_column(Text, default="")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    min_len: Mapped[int] = mapped_column(Integer, default=1)
    max_len: Mapped[int] = mapped_column(Integer, default=200)
    min_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def option_list(self) -> list[str]:
        return [o.strip() for o in (self.options or "").splitlines() if o.strip()]


# ---------------------------------------------------------------------------
# Ishtirokchilar va natijalar
# ---------------------------------------------------------------------------
class Participant(Base):
    __tablename__ = "participants"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, nullable=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    tg_username: Mapped[str] = mapped_column(String(64), default="")
    lang: Mapped[str] = mapped_column(String(8), default="lat")  # lat | cyr
    first_name: Mapped[str] = mapped_column(String(64))
    last_name: Mapped[str] = mapped_column(String(64))
    phone: Mapped[str] = mapped_column(String(20), unique=True)  # bitta raqam = bitta o'quvchi
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id", ondelete="RESTRICT"))
    grade_id: Mapped[int] = mapped_column(ForeignKey("grades.id", ondelete="RESTRICT"))
    address: Mapped[str] = mapped_column(String(256), default="")
    instagram_username: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disqualified
    note: Mapped[str] = mapped_column(Text, default="")
    search_text: Mapped[str] = mapped_column(Text, default="")  # qidiruv uchun lotin, kichik harf
    bot_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    school: Mapped["School"] = relationship(lazy="selectin")
    grade: Mapped["Grade"] = relationship(lazy="selectin")
    subjects: Mapped[list["Subject"]] = relationship(secondary=participant_subjects, lazy="selectin")
    answers: Mapped[list["CustomAnswer"]] = relationship(
        back_populates="participant", lazy="selectin", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_participants_school", "school_id"),
        Index("ix_participants_grade", "grade_id"),
    )

    @property
    def full_name(self) -> str:
        return f"{self.last_name} {self.first_name}".strip()


class CustomAnswer(Base):
    __tablename__ = "custom_answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    question_id: Mapped[int] = mapped_column(ForeignKey("custom_questions.id", ondelete="CASCADE"))
    value: Mapped[str] = mapped_column(Text, default="")
    participant: Mapped["Participant"] = relationship(back_populates="answers")
    question: Mapped["CustomQuestion"] = relationship(lazy="selectin")
    __table_args__ = (UniqueConstraint("participant_id", "question_id"),)


class Result(Base):
    """Bitta ishtirokchining bitta fandan bitta turdagi natijasi."""
    __tablename__ = "results"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    details: Mapped[str] = mapped_column(Text, default="{}")  # guruhlar bo'yicha to'g'ri javoblar (JSON)
    correct_total: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float, default=0)
    max_score: Mapped[float] = mapped_column(Float, default=0)
    percent: Mapped[float] = mapped_column(Float, default=0)
    entered_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entered_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    participant: Mapped["Participant"] = relationship(lazy="selectin")
    entered_by: Mapped[Optional["User"]] = relationship(lazy="selectin")

    __table_args__ = (
        UniqueConstraint("participant_id", "subject_id", "round_id"),
        Index("ix_results_subject_round", "subject_id", "round_id"),
    )


class Attendance(Base):
    """Davomat: o'quvchi olimpiadaga (shu turga) keldimi."""
    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"))
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id", ondelete="CASCADE"))
    present: Mapped[bool] = mapped_column(Boolean, default=True)  # True — keldi, False — kelmadi
    marked_by: Mapped[str] = mapped_column(String(64), default="")
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

    __table_args__ = (
        UniqueConstraint("participant_id", "round_id"),
        Index("ix_attendance_round", "round_id", "present"),
    )


class Appeal(Base):
    """Natijaga e'tiroz (apellyatsiya)."""
    __tablename__ = "appeals"
    id: Mapped[int] = mapped_column(primary_key=True)
    participant_id: Mapped[int] = mapped_column(ForeignKey("participants.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    round_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rounds.id", ondelete="SET NULL"), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)  # new | accepted | rejected
    answer: Mapped[str] = mapped_column(Text, default="")
    answered_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    participant: Mapped["Participant"] = relationship(lazy="selectin")
    subject: Mapped[Optional["Subject"]] = relationship(lazy="selectin")
    round: Mapped[Optional["Round"]] = relationship(lazy="selectin")


# ---------------------------------------------------------------------------
# Sertifikat / diplom shablonlari
# ---------------------------------------------------------------------------
class CertificateTemplate(Base):
    __tablename__ = "certificate_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    background_path: Mapped[str] = mapped_column(String(256), default="")
    width: Mapped[int] = mapped_column(Integer, default=1754)
    height: Mapped[int] = mapped_column(Integer, default=1240)
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    scope: Mapped[str] = mapped_column(String(16), default="subject")  # subject | block
    subject_id: Mapped[Optional[int]] = mapped_column(ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True)
    block_id: Mapped[Optional[int]] = mapped_column(ForeignKey("blocks.id", ondelete="SET NULL"), nullable=True)
    round_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rounds.id", ondelete="SET NULL"), nullable=True)
    rule: Mapped[str] = mapped_column(String(16), default="all")  # all | top | percent
    top_n: Mapped[int] = mapped_column(Integer, default=3)
    min_percent: Mapped[float] = mapped_column(Float, default=0)
    position: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Ommaviy xabarlar, jurnal, bot holatlari
# ---------------------------------------------------------------------------
class Broadcast(Base):
    __tablename__ = "broadcasts"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), default="message")  # message | results | certificates
    text: Mapped[str] = mapped_column(Text, default="")
    text_cyr: Mapped[str] = mapped_column(Text, default="")
    photo_path: Mapped[str] = mapped_column(String(256), default="")
    filters_json: Mapped[str] = mapped_column(Text, default="{}")
    round_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rounds.id", ondelete="SET NULL"), nullable=True)
    # scheduled | sending | done | cancelled
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    cursor_id: Mapped[int] = mapped_column(Integer, default=0)  # qayta ishga tushganda davom ettirish
    total: Mapped[int] = mapped_column(Integer, default=0)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(32))
    entity: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(32), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    old: Mapped[str] = mapped_column(Text, default="")
    new: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)


class FsmRecord(Base):
    """Bot holatlari (FSM) — server qayta ishga tushsa ham o'quvchi joyidan davom etadi."""
    __tablename__ = "fsm_records"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

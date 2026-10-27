"""Ball va foizni hisoblash.

Hakam faqat to'g'ri javoblar sonini (yoki 'manual' rejimda ballni) kiritadi,
qolganini shu modul hisoblaydi va tekshiradi.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Subject


class ScoreError(ValueError):
    """Noto'g'ri kiritilgan qiymat (foydalanuvchiga ko'rsatiladi)."""


@dataclass
class Computed:
    details: dict = field(default_factory=dict)
    correct_total: int = 0
    score: float = 0.0
    max_score: float = 0.0
    percent: float = 0.0


def _parse_int(raw: str, label: str) -> int:
    raw = (raw or "").strip().replace(",", ".")
    if raw == "":
        raise ScoreError(f"{label}: qiymat kiritilmagan")
    try:
        val = float(raw)
    except ValueError:
        raise ScoreError(f"{label}: son kiriting")
    if val != int(val):
        raise ScoreError(f"{label}: butun son bo'lishi kerak")
    return int(val)


def _parse_float(raw: str, label: str) -> float:
    raw = (raw or "").strip().replace(",", ".")
    if raw == "":
        raise ScoreError(f"{label}: qiymat kiritilmagan")
    try:
        return float(raw)
    except ValueError:
        raise ScoreError(f"{label}: son kiriting")


def compute(subject: Subject, values: dict[str, str]) -> Computed:
    """values — formadan kelgan qiymatlar:
      uniform: {"correct": "14"}
      groups:  {"g12": "5", "g13": "3"}  (g + guruh id)
      manual:  {"score": "72.5"}
    """
    max_score = subject.max_score
    if max_score <= 0:
        raise ScoreError("Fanning maksimal bali 0 — fan sozlamalarini tekshiring")

    if subject.scoring_mode == "groups":
        if not subject.groups:
            raise ScoreError("Fanda ball guruhlari kiritilmagan")
        details, score, correct = {}, 0.0, 0
        for g in subject.groups:
            n = _parse_int(values.get(f"g{g.id}", ""), g.name)
            if not 0 <= n <= g.question_count:
                raise ScoreError(f"{g.name}: 0 dan {g.question_count} gacha bo'lishi kerak")
            details[f"g{g.id}"] = n
            correct += n
            score += n * g.points
        return Computed(details, correct, round(score, 2), max_score,
                        round(score / max_score * 100, 2))

    if subject.scoring_mode == "manual":
        score = _parse_float(values.get("score", ""), "Ball")
        if not 0 <= score <= max_score:
            raise ScoreError(f"Ball 0 dan {max_score:g} gacha bo'lishi kerak")
        return Computed({"score": score}, 0, round(score, 2), max_score,
                        round(score / max_score * 100, 2))

    # uniform
    n = _parse_int(values.get("correct", ""), "To'g'ri javoblar")
    if not 0 <= n <= subject.total_questions:
        raise ScoreError(f"To'g'ri javoblar 0 dan {subject.total_questions} gacha bo'lishi kerak")
    score = n * subject.points_per_question
    return Computed({"correct": n}, n, round(score, 2), max_score,
                    round(score / max_score * 100, 2))

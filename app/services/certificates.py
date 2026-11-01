"""Sertifikat, diplom va ruxsatnomalar: shablonlar, o'rinlar (mukofotlar) va tayyorlash.

O'rin (mukofot) qoidalari — «🏅 O'rinlar va mukofotlar» sahifasida:
  * har fan/blok uchun: nechta sovrinli o'rin (masalan 3) va minimal foiz (masalan 50%);
  * reyting ballga qarab avtomatik (teng ball — teng o'rin: 1, 2, 2, 4);
  * sovrindor: o'rni ≤ sovrinli o'rinlar soni VA foizi ≥ minimal foiz.
Shablon «kimga» qoidasi: hammaga / sovrindorlarga / faqat N-o'ringa / ishtirokchilarga
(sovrindor bo'lmaganlar) / 1..N o'ringa (shartsiz) / foiz bo'yicha.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import Block, CertificateTemplate, Participant, Round
from ..utils import by_lang, fmt_num, lat2cyr
from . import design as dz
from . import design_presets as presets
from . import ranking
from . import settings as st

# ---------------------------------------------------------------------------
# O'zgaruvchilar ({...}) — muharrirdagi tugmalar va namuna qiymatlar
# ---------------------------------------------------------------------------
COMMON_PH: dict[str, tuple[str, str]] = {
    "full_name": ("Familiya Ism", "Aliyev Azizbek"),
    "first_name": ("Ism", "Azizbek"),
    "last_name": ("Familiya", "Aliyev"),
    "school": ("Maktab", "5-maktab"),
    "grade": ("Sinf", "9"),
    "code": ("ID raqam", "0017"),
    "olympiad": ("Olimpiada nomi", ""),
    "round": ("Tur", "1-tur"),
    "date": ("Bugungi sana", ""),
}
CERT_PH: dict[str, tuple[str, str]] = {
    **COMMON_PH,
    "subject": ("Fan yoki blok", "Matematika"),
    "place": ("O'rin (raqam)", "1"),
    "place_roman": ("O'rin (rim raqami)", "I"),
    "place_text": ("O'rin («1-o'rin»)", "1-o'rin"),
    "award": ("Mukofot («1-o'rin g'olibi» / «faol ishtirokchisi»)", "1-o'rin g'olibi"),
    "score": ("Ball", "57"),
    "max_score": ("Maksimal ball", "60"),
    "percent": ("Foiz", "95"),
}
PASS_PH: dict[str, tuple[str, str]] = {
    **COMMON_PH,
    "subjects": ("Fanlar", "Matematika, Fizika"),
    "exam_date": ("Olimpiada sanasi", "15-noyabr 2026"),
    "weekday": ("Hafta kuni", "shanba"),
    "arrive_time": ("Kelish vaqti", "08:30"),
    "start_time": ("Boshlanish vaqti", "09:00"),
    "end_time": ("Tugash vaqti", "11:00"),
    "duration": ("Davomiyligi", "120 daqiqa"),
    "session": ("Smena", "1-smena"),
    "building": ("Bino", "Haziniy maktabi"),
    "address": ("Manzil", "Uchko'prik tumani, Oltin vodiy MFY"),
    "room": ("Xona", "204-xona"),
    "floor": ("Qavat", "2"),
    "row": ("Qator", "3"),
    "desk": ("Parta", "2"),
    "seat_side": ("Partadagi o'rni (chap/o'ng)", "chap"),
    "seat_no": ("O'rin raqami (xonada)", "14"),
    "seat_text": ("O'rin to'liq («14-o'rin · 3-qator, 2-parta, chap»)", "14-o'rin · 3-qator, 2-parta, chap"),
}

ROMAN = {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X"}

RULES = {
    "all": "Natijasi bor barcha ishtirokchilar",
    "winners": "Sovrindorlar (mukofot qoidasi bo'yicha)",
    "place": "Faqat N-o'rin sovrindorlari",
    "participants": "Sovrindor bo'lmaganlar (ishtirokchi sertifikati)",
    "top": "1–N o'rin (foiz shartisiz)",
    "percent": "Foiz bo'yicha",
}

AWARD_TEXT_DEFAULTS = {
    "award_text_1": "1-o'rin g'olibi",
    "award_text_2": "2-o'rin sovrindori",
    "award_text_3": "3-o'rin sovrindori",
    "award_text_n": "{place}-o'rin sovrindori",
    "award_text_none": "faol ishtirokchisi",
}


def sample_context(kind: str = "certificate") -> dict:
    ph = PASS_PH if kind == "pass" else CERT_PH
    ctx = {k: v[1] for k, v in ph.items()}
    ctx["olympiad"] = st.get("olympiad_name")
    ctx["date"] = datetime.now(config.TZ).strftime("%d.%m.%Y")
    return ctx


# ---------------------------------------------------------------------------
# Mukofot (o'rin) qoidalari
# ---------------------------------------------------------------------------
def award_rules() -> dict:
    """{"default": {"places": 3, "min_percent": 0}, "s:<fan_id>": {...}, "b:<blok_id>": {...}}"""
    try:
        data = json.loads(st.raw("award_rules") or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("default", {"places": 3, "min_percent": 0})
    return data


def rule_for(scope_key: str) -> tuple[int, float]:
    """(sovrinli o'rinlar soni, minimal foiz) — fan/blok uchun alohida yoki umumiy."""
    rules = award_rules()
    r = rules.get(scope_key) or rules["default"]
    try:
        return max(0, int(r.get("places", 3))), max(0.0, float(r.get("min_percent", 0)))
    except (TypeError, ValueError):
        return 3, 0.0


def prize_place(row: ranking.RankRow, scope_key: str) -> Optional[int]:
    """Sovrinli o'rin (1, 2, 3 ...) yoki None — sovrindor emas."""
    places, min_pct = rule_for(scope_key)
    if row.place <= places and row.percent + 1e-9 >= min_pct and row.score > 0:
        return row.place
    return None


def award_text(prize: Optional[int], lang: str) -> str:
    if prize is None:
        key = "award_text_none"
    elif prize in (1, 2, 3):
        key = f"award_text_{prize}"
    else:
        key = "award_text_n"
    text = (st.raw(key) or AWARD_TEXT_DEFAULTS[key]).replace("{place}", str(prize or ""))
    return lat2cyr(text) if lang == "cyr" else text


def place_text(place: int, lang: str) -> str:
    return f"{place}-ўрин" if lang == "cyr" else f"{place}-o'rin"


def rule_matches(tpl: CertificateTemplate, row: ranking.RankRow, prize: Optional[int]) -> bool:
    """Shablon shu ishtirokchiga (shu fan/blok bo'yicha) beriladimi."""
    rule = tpl.rule
    if rule == "winners":
        return prize is not None
    if rule == "place":
        return prize is not None and prize == max(1, tpl.top_n)
    if rule == "participants":
        return prize is None
    if rule == "top":
        return row.place <= max(1, tpl.top_n)
    if rule == "percent":
        return row.percent + 1e-9 >= tpl.min_percent
    return True  # all


# ---------------------------------------------------------------------------
# Dizayn
# ---------------------------------------------------------------------------
def load_fields(tpl: CertificateTemplate) -> list[dict]:
    """Eski (1-versiya) maydonlar — faqat yangi dizaynga o'girish uchun."""
    try:
        data = json.loads(tpl.fields_json or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def load_design(tpl: CertificateTemplate) -> dict:
    """Shablon dizayni (tekshirilgan). Eski shablon bo'lsa — o'girib beradi (saqlamaydi)."""
    if tpl.design_json:
        try:
            return dz.clean_design(json.loads(tpl.design_json))
        except (ValueError, TypeError):
            pass
    if tpl.kind == "pass":
        return presets.build("pass_default")
    return dz.from_legacy(load_fields(tpl), tpl.background_path, tpl.width or 1754, tpl.height or 1240)


def save_design(tpl: CertificateTemplate, design: dict) -> None:
    design = dz.clean_design(design)
    tpl.design_json = json.dumps(design, ensure_ascii=False, separators=(",", ":"))
    tpl.width, tpl.height = design["page"]["w"], design["page"]["h"]


def render_template(tpl: CertificateTemplate, ctx: dict) -> Image.Image:
    return dz.render(load_design(tpl), ctx)


def template_dpi(tpl: CertificateTemplate) -> int:
    return load_design(tpl)["page"].get("dpi", 150)


# ---------------------------------------------------------------------------
# Ishtirokchining sertifikatlari
# ---------------------------------------------------------------------------
def build_context(p: Participant, title: str, row: ranking.RankRow, round_: Round,
                  prize: Optional[int]) -> dict:
    lang = p.lang
    return {
        "full_name": p.full_name, "first_name": p.first_name, "last_name": p.last_name,
        "school": by_lang(p.school.name, p.school.name_cyr, lang) if p.school else "",
        "grade": by_lang(p.grade.name, p.grade.name_cyr, lang) if p.grade else "",
        "subject": title, "place": str(row.place), "place_roman": ROMAN.get(row.place, str(row.place)),
        "place_text": place_text(row.place, lang), "award": award_text(prize, lang),
        "score": fmt_num(row.score), "max_score": fmt_num(row.max_score), "percent": fmt_num(row.percent),
        "round": by_lang(round_.name, round_.name_cyr, lang), "code": p.code or "",
        "olympiad": st.get("olympiad_name"),
        "date": datetime.now(config.TZ).strftime("%d.%m.%Y"),
    }


@dataclass
class CertItem:
    template: CertificateTemplate
    title: str
    context: dict
    place: int = 0
    prize: Optional[int] = None

    @property
    def filename(self) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in self.title)[:40]
        return f"{self.context.get('code', '')}_{safe}_{self.template.id}.pdf"

    def render(self) -> Image.Image:
        return render_template(self.template, self.context)


async def active_templates(session: AsyncSession) -> list[CertificateTemplate]:
    return (await session.execute(
        select(CertificateTemplate).where(CertificateTemplate.is_active == True,  # noqa: E712
                                          CertificateTemplate.kind == "certificate")
        .order_by(CertificateTemplate.position, CertificateTemplate.id))).scalars().all()


async def certificates_for(session: AsyncSession, p: Participant,
                           templates: Optional[list[CertificateTemplate]] = None) -> list[CertItem]:
    """Ishtirokchiga tegishli barcha sertifikatlar (faol shablonlar va mukofot qoidalari bo'yicha)."""
    if p.status != "active":
        return []
    if templates is None:
        templates = await active_templates(session)
    if not templates:
        return []
    by_grade = st.get("ranking_by_grade")
    cur = await ranking.current_round(session)
    items: list[CertItem] = []
    my_subjects = {s.id: s for s in p.subjects}
    for tpl in templates:
        round_ = (await session.get(Round, tpl.round_id)) if tpl.round_id else cur
        if round_ is None:
            continue
        if tpl.scope == "block":
            blocks = [await session.get(Block, tpl.block_id)] if tpl.block_id else \
                (await session.execute(select(Block).where(Block.is_active == True))).scalars().all()  # noqa: E712
            for b in blocks:
                if b is None or not set(my_subjects) & {s.id for s in b.subjects}:
                    continue
                rows = await ranking.block_ranking(session, b, round_, by_grade=by_grade)
                row = next((r for r in rows if r.participant.id == p.id), None)
                if row is None:
                    continue
                prize = prize_place(row, f"b:{b.id}")
                if rule_matches(tpl, row, prize):
                    title = by_lang(b.name, b.name_cyr, p.lang)
                    items.append(CertItem(tpl, title, build_context(p, title, row, round_, prize), row.place, prize))
        else:
            subjects = [my_subjects[tpl.subject_id]] if tpl.subject_id in my_subjects else \
                ([] if tpl.subject_id else list(my_subjects.values()))
            for s in subjects:
                rows = await ranking.subject_ranking(session, s, round_, by_grade=by_grade)
                row = next((r for r in rows if r.participant.id == p.id), None)
                if row is None:
                    continue
                prize = prize_place(row, f"s:{s.id}")
                if rule_matches(tpl, row, prize):
                    title = by_lang(s.name, s.name_cyr, p.lang)
                    items.append(CertItem(tpl, title, build_context(p, title, row, round_, prize), row.place, prize))
    return items


def caption_for(item: CertItem, lang: str) -> str:
    """Botdagi izoh: sovrindorga tabrik bilan, qolganlarga oddiy."""
    from ..bot.texts import t
    if item.prize is not None:
        return t("cert_caption_place", lang).format(title=item.title, place=item.prize)
    return t("cert_caption", lang).format(title=item.title)


def new_template(kind: str, preset: str, name: str = "") -> CertificateTemplate:
    """Tayyor shablondan yangi shablon (bazaga qo'shilmagan)."""
    plist = presets.PASS_PRESETS if kind == "pass" else presets.CERT_PRESETS
    if preset not in plist:
        preset = next(iter(plist))
    tpl = CertificateTemplate(name=name or plist[preset][0], kind=kind, is_active=False,
                              rule="all", scope="subject", top_n=3)
    if kind == "certificate" and preset.startswith("diploma_"):
        tpl.rule, tpl.top_n = "place", int(preset[-1])
    save_design(tpl, presets.build(preset))
    return tpl

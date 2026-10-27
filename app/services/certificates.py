"""Sertifikat va diplomlarni yaratish (Pillow).

Shablon = fon rasmi + matn maydonlari. Har bir maydon:
  {"text": "{full_name}", "x": 50, "y": 45, "size": 4.5, "color": "#1f2937",
   "align": "center", "font": "serif-bold", "max_width": 80}
  x, y, size, max_width — rasm kengligi/balandligiga nisbatan foizda,
  shuning uchun dizayner sahifasidagi joylashuv rasmdagi bilan bir xil bo'ladi.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import Block, CertificateTemplate, Participant, Round
from ..utils import by_lang, fmt_num
from . import ranking
from . import settings as st

FONT_DIR = Path(__file__).resolve().parent.parent / "web" / "static" / "fonts"
FONTS = {
    "sans": "DejaVuSans.ttf",
    "sans-bold": "DejaVuSans-Bold.ttf",
    "serif": "DejaVuSerif.ttf",
    "serif-bold": "DejaVuSerif-Bold.ttf",
}
PLACEHOLDERS = {
    "{full_name}": "Familiya Ism",
    "{first_name}": "Ism",
    "{last_name}": "Familiya",
    "{school}": "Maktab",
    "{grade}": "Sinf",
    "{subject}": "Fan yoki blok nomi",
    "{place}": "O'rin (raqam)",
    "{score}": "Ball",
    "{max_score}": "Maksimal ball",
    "{percent}": "Foiz",
    "{round}": "Tur nomi",
    "{code}": "Ishtirokchi ID",
    "{olympiad}": "Olimpiada nomi",
    "{date}": "Sana",
}
DEFAULT_FIELDS = [
    {"text": "SERTIFIKAT", "x": 50, "y": 22, "size": 6.5, "color": "#7a5c1e", "align": "center",
     "font": "serif-bold", "max_width": 80},
    {"text": "{olympiad}", "x": 50, "y": 33, "size": 2.4, "color": "#374151", "align": "center",
     "font": "sans", "max_width": 80},
    {"text": "{full_name}", "x": 50, "y": 47, "size": 4.6, "color": "#111827", "align": "center",
     "font": "serif-bold", "max_width": 80},
    {"text": "{school}, {grade}-sinf", "x": 50, "y": 56, "size": 2.0, "color": "#374151",
     "align": "center", "font": "sans", "max_width": 80},
    {"text": "{subject} fanidan {place}-o'rin", "x": 50, "y": 64, "size": 2.8, "color": "#7a5c1e",
     "align": "center", "font": "serif-bold", "max_width": 80},
    {"text": "Ball: {score} / {max_score} ({percent}%)", "x": 50, "y": 71, "size": 2.0,
     "color": "#374151", "align": "center", "font": "sans", "max_width": 80},
    {"text": "{date}", "x": 18, "y": 86, "size": 1.6, "color": "#4b5563", "align": "left",
     "font": "sans", "max_width": 30},
    {"text": "ID: {code}", "x": 82, "y": 86, "size": 1.6, "color": "#4b5563", "align": "right",
     "font": "sans", "max_width": 30},
]


@lru_cache(maxsize=64)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / FONTS.get(name, FONTS["sans"])), max(size, 6))


def default_background_path() -> Path:
    """Fon yuklanmagan shablonlar uchun chiroyli standart fon (bir marta yaratiladi)."""
    path = config.UPLOAD_DIR / "certificates" / "default_bg.png"
    if path.exists():
        return path
    w, h = 1754, 1240  # A4 albom, 150 dpi
    img = Image.new("RGB", (w, h), "#fffdf6")
    d = ImageDraw.Draw(img)
    gold, dark = "#b08d3c", "#7a5c1e"
    d.rectangle([30, 30, w - 31, h - 31], outline=dark, width=10)
    d.rectangle([55, 55, w - 56, h - 56], outline=gold, width=3)
    d.rectangle([70, 70, w - 71, h - 71], outline=gold, width=1)
    # Burchak bezaklari
    for cx, cy in ((70, 70), (w - 71, 70), (70, h - 71), (w - 71, h - 71)):
        for r in (40, 28, 16):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=gold, width=2)
    # Yuqori va pastki chiziq bezagi
    d.line([w * 0.3, 360, w * 0.7, 360], fill=gold, width=3)
    d.line([w * 0.35, h - 230, w * 0.65, h - 230], fill=gold, width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return path


def load_fields(tpl: CertificateTemplate) -> list[dict]:
    try:
        data = json.loads(tpl.fields_json or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


class _Safe(dict):
    def __missing__(self, key):  # noma'lum {placeholder} o'zgarishsiz qoladi
        return "{" + key + "}"


def render(tpl: CertificateTemplate, ctx: dict) -> Image.Image:
    """Shablon va ma'lumotlardan rasm yaratadi."""
    bg_path = Path(tpl.background_path) if tpl.background_path else None
    if not bg_path or not bg_path.exists():
        bg_path = default_background_path()
    img = Image.open(bg_path).convert("RGB")
    W, H = img.size
    d = ImageDraw.Draw(img)
    for f in load_fields(tpl):
        try:
            text = str(f.get("text", "")).format_map(_Safe(ctx))
        except (ValueError, IndexError):
            text = str(f.get("text", ""))  # noto'g'ri qavslar bo'lsa matn o'zi chiqadi
        if not text.strip():
            continue
        size = max(6, int(float(f.get("size", 3)) * W / 100))
        font_name = f.get("font", "sans")
        font = _font(font_name, size)
        max_w = float(f.get("max_width", 0) or 0) * W / 100
        # Matn sig'masa shriftni kichraytiramiz
        if max_w > 0:
            while size > 8 and d.textlength(text, font=font) > max_w:
                size = int(size * 0.94)
                font = _font(font_name, size)
        x = float(f.get("x", 50)) * W / 100
        y = float(f.get("y", 50)) * H / 100
        anchor = {"left": "lm", "right": "rm"}.get(f.get("align"), "mm")
        d.text((x, y), text, font=font, fill=f.get("color", "#000000"), anchor=anchor)
    return img


def to_pdf(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, "PDF", resolution=150.0)
    return buf.getvalue()


def to_png(img: Image.Image, max_width: int = 1200) -> bytes:
    if img.width > max_width:
        img = img.resize((max_width, int(img.height * max_width / img.width)))
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def sample_context() -> dict:
    return {
        "full_name": "Aliyev Aziz", "first_name": "Aziz", "last_name": "Aliyev",
        "school": "5-maktab", "grade": "9", "subject": "Matematika", "place": "1",
        "score": "57", "max_score": "60", "percent": "95", "round": "1-tur",
        "code": "0017", "olympiad": st.get("olympiad_name"),
        "date": datetime.now(config.TZ).strftime("%d.%m.%Y"),
    }


def build_context(p: Participant, title: str, row: ranking.RankRow, round_: Round) -> dict:
    lang = p.lang
    return {
        "full_name": p.full_name, "first_name": p.first_name, "last_name": p.last_name,
        "school": by_lang(p.school.name, p.school.name_cyr, lang) if p.school else "",
        "grade": by_lang(p.grade.name, p.grade.name_cyr, lang) if p.grade else "",
        "subject": title, "place": str(row.place), "score": fmt_num(row.score),
        "max_score": fmt_num(row.max_score), "percent": fmt_num(row.percent),
        "round": by_lang(round_.name, round_.name_cyr, lang), "code": p.code or "",
        "olympiad": st.get("olympiad_name"),
        "date": datetime.now(config.TZ).strftime("%d.%m.%Y"),
    }


def eligible(tpl: CertificateTemplate, row: ranking.RankRow) -> bool:
    if tpl.rule == "top":
        return row.place <= max(1, tpl.top_n)
    if tpl.rule == "percent":
        return row.percent >= tpl.min_percent
    return True


@dataclass
class CertItem:
    template: CertificateTemplate
    title: str
    context: dict

    @property
    def filename(self) -> str:
        safe = "".join(c if c.isalnum() else "_" for c in self.title)[:40]
        return f"{self.context.get('code', '')}_{safe}_{self.template.id}.pdf"


async def certificates_for(session: AsyncSession, p: Participant) -> list[CertItem]:
    """Ishtirokchiga tegishli barcha sertifikatlar (faol shablonlar bo'yicha)."""
    if p.status != "active":
        return []
    templates = (await session.execute(
        select(CertificateTemplate).where(CertificateTemplate.is_active == True)
        .order_by(CertificateTemplate.position, CertificateTemplate.id))).scalars().all()
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
                (await session.execute(select(Block).where(Block.is_active == True))).scalars().all()
            for b in blocks:
                if b is None or not set(my_subjects) & {s.id for s in b.subjects}:
                    continue
                rows = await ranking.block_ranking(session, b, round_, by_grade=by_grade)
                row = next((r for r in rows if r.participant.id == p.id), None)
                if row and eligible(tpl, row):
                    title = by_lang(b.name, b.name_cyr, p.lang)
                    items.append(CertItem(tpl, title, build_context(p, title, row, round_)))
        else:
            subjects = [my_subjects[tpl.subject_id]] if tpl.subject_id in my_subjects else \
                ([] if tpl.subject_id else list(my_subjects.values()))
            for s in subjects:
                rows = await ranking.subject_ranking(session, s, round_, by_grade=by_grade)
                row = next((r for r in rows if r.participant.id == p.id), None)
                if row and eligible(tpl, row):
                    title = by_lang(s.name, s.name_cyr, p.lang)
                    items.append(CertItem(tpl, title, build_context(p, title, row, round_)))
    return items


def save_background(data: bytes, tpl_id: int) -> tuple[str, int, int]:
    """Yuklangan fon rasmini tekshirib saqlaydi. (yo'l, kenglik, balandlik)"""
    img = Image.open(BytesIO(data))
    img.load()
    img = img.convert("RGB")
    # Juda katta rasmlarni kichraytiramiz (A4 300 dpi chegarasi)
    if max(img.size) > 3508:
        k = 3508 / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)))
    path = config.UPLOAD_DIR / "certificates" / f"bg_{tpl_id}_{int(datetime.now().timestamp())}.png"
    img.save(path, optimize=True)
    return str(path), img.width, img.height


def image_size(path: Optional[str]) -> tuple[int, int]:
    p = Path(path) if path else None
    if not p or not p.exists():
        p = default_background_path()
    with Image.open(p) as im:
        return im.size

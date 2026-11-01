"""Tayyor dizayn shablonlari (bir bosishda qo'yiladi, keyin istalgancha o'zgartiriladi).

Hammasi faqat shakl va matnlardan iborat — tashqi rasm kerak emas.
"""
from __future__ import annotations

from typing import Callable

from . import design as dz


def T(text, x, y, w, h, size, color="#111827", font="montserrat", bold=False, align="center", **kw) -> dict:
    """Matn elementi (qisqartma)."""
    el = {"type": "text", "text": text, "x": x, "y": y, "w": w, "h": h, "size": size, "color": color,
          "font": font, "bold": bold, "align": align, "valign": kw.pop("valign", "middle"),
          "wrap": kw.pop("wrap", True), "fit": kw.pop("fit", True), "lh": kw.pop("lh", 1.2),
          "ls": kw.pop("ls", 0), "upper": kw.pop("upper", False)}
    el.update(kw)
    return el


def R(x, y, w, h, fill="", stroke="", sw=0.2, radius=0, **kw) -> dict:
    return {"type": "rect", "x": x, "y": y, "w": w, "h": h, "fill": fill, "stroke": stroke, "sw": sw,
            "radius": radius, **kw}


def E(x, y, w, h, fill="", stroke="", sw=0.2, **kw) -> dict:
    return {"type": "ellipse", "x": x, "y": y, "w": w, "h": h, "fill": fill, "stroke": stroke, "sw": sw, **kw}


def L(x, y, w, stroke="#111827", sw=0.15, **kw) -> dict:
    return {"type": "line", "x": x, "y": y - 1, "w": w, "h": 2, "stroke": stroke, "sw": sw, **kw}


def _page(preset="A4L") -> dict:
    return {"preset": preset, "dpi": 150}


def _named(els: list[dict]) -> list[dict]:
    for i, e in enumerate(els, 1):
        e.setdefault("id", f"e{i}")
    return els


# ---------------------------------------------------------------------------
# Sertifikat va diplomlar
# ---------------------------------------------------------------------------
def classic_gold() -> dict:
    gold, dark = "#b08d3c", "#7a5c1e"
    corners = []
    for cx, cy in ((4.5, 6.4), (95.5, 6.4), (4.5, 93.6), (95.5, 93.6)):
        for r, sw in ((3.2, 0.12), (2.2, 0.12), (1.2, 0.12)):
            corners.append(E(cx - r, cy - r * 1754 / 1240, 2 * r, 2 * r * 1754 / 1240, stroke=gold, sw=sw))
    return {"page": _page(), "bg": {"type": "color", "color": "#fffaf0"}, "els": _named([
        R(1.7, 2.4, 96.6, 95.2, stroke=dark, sw=0.55),
        R(3.1, 4.4, 93.8, 91.2, stroke=gold, sw=0.18),
        R(4.0, 5.6, 92.0, 88.8, stroke=gold, sw=0.06),
        *corners,
        T("SERTIFIKAT", 10, 13, 80, 13, 6.4, dark, "cormorant", True, ls=0.18, name="Sarlavha"),
        T("{olympiad}", 15, 27, 70, 6, 2.1, "#374151", "montserrat", name="Olimpiada nomi"),
        L(35, 36, 30, gold, 0.18),
        T("Ushbu sertifikat bilan taqdirlanadi", 20, 38.5, 60, 5, 1.6, "#6b7280", "montserrat", name="Izoh"),
        T("{full_name}", 10, 44, 80, 12, 4.6, "#111827", "cormorant", True, wrap=False, name="Familiya Ism"),
        T("{school}, {grade}-sinf o'quvchisi", 15, 57, 70, 5, 1.8, "#374151", "montserrat", name="Maktab"),
        T("{subject} fanidan {award}", 12, 63, 76, 7, 2.6, dark, "cormorant", True, name="Mukofot"),
        T("Ball: {score} / {max_score} ({percent}%)", 25, 70.5, 50, 5, 1.6, "#4b5563", "montserrat", name="Ball"),
        L(12, 84, 22, "#9ca3af", 0.08),
        T("Sana: {date}", 12, 85, 22, 4, 1.25, "#4b5563", "montserrat", name="Sana"),
        L(66, 84, 22, "#9ca3af", 0.08),
        T("Tashkiliy qo'mita raisi", 66, 85, 22, 4, 1.25, "#4b5563", "montserrat", name="Imzo"),
        T("ID: {code}", 40, 86.5, 20, 4, 1.1, "#9ca3af", "montserrat", name="ID"),
    ])}


def modern_blue() -> dict:
    navy, blue = "#1e3a8a", "#3b82f6"
    return {"page": _page(), "bg": {"type": "color", "color": "#ffffff"}, "els": _named([
        R(0, 0, 30, 100, fill=navy, name="Chap tasma"),
        E(-12, -14, 36, 50, fill="#ffffff", opacity=0.07),
        E(8, 62, 30, 42, fill="#ffffff", opacity=0.07),
        R(30, 0, 1.2, 100, fill=blue),
        T("{olympiad}", 3, 38, 24, 24, 2.2, "#ffffff", "montserrat", True, name="Olimpiada nomi"),
        T("SERTIFIKAT", 36, 12, 58, 12, 6.0, navy, "oswald", True, "left", ls=0.08, name="Sarlavha"),
        T("Ushbu sertifikat bilan taqdirlanadi:", 36, 27, 58, 5, 1.7, "#64748b", "montserrat", align="left"),
        T("{full_name}", 36, 34, 58, 12, 4.4, "#0f172a", "montserrat", True, "left", wrap=False, name="Familiya Ism"),
        L(36, 48, 20, blue, 0.35),
        T("{school}, {grade}-sinf o'quvchisi", 36, 51, 58, 5, 1.8, "#334155", "montserrat", align="left"),
        T("{subject} fanidan {award}", 36, 58, 58, 7, 2.5, navy, "montserrat", True, "left", name="Mukofot"),
        T("Ball: {score} / {max_score} ({percent}%)", 36, 66, 58, 5, 1.6, "#475569", "montserrat", align="left"),
        T("Sana: {date}", 36, 86, 25, 4, 1.25, "#64748b", "montserrat", align="left"),
        T("ID: {code}", 70, 86, 24, 4, 1.25, "#94a3b8", "montserrat", align="right"),
    ])}


def green_nature() -> dict:
    green, dark = "#059669", "#065f46"
    return {"page": _page(), "bg": {"type": "gradient", "color": "#f0fdf4", "color2": "#d1fae5", "angle": 160},
            "els": _named([
                R(2.5, 3.5, 95, 93, stroke=green, sw=0.7, radius=6),
                R(4.2, 5.9, 91.6, 88.2, stroke="#6ee7b7", sw=0.15, radius=5),
                E(84, 9, 9, 12.7, fill="#a7f3d0"), E(88, 15, 6, 8.5, fill="#6ee7b7"),
                E(6, 79, 8, 11.3, fill="#a7f3d0"), E(10, 84, 5, 7, fill="#6ee7b7"),
                T("SERTIFIKAT", 15, 11, 70, 13, 6.0, dark, "lora", True, ls=0.12, name="Sarlavha"),
                T("{olympiad}", 15, 25, 70, 6, 2.0, "#065f46", "lora", name="Olimpiada nomi"),
                T("{full_name}", 10, 38, 80, 13, 4.8, "#064e3b", "caveat", True, wrap=False, name="Familiya Ism"),
                L(30, 52.5, 40, green, 0.15),
                T("{school}, {grade}-sinf o'quvchisi", 15, 55, 70, 5, 1.8, "#374151", "lora"),
                T("{subject} fanidan {award}", 12, 62, 76, 7, 2.6, dark, "lora", True, name="Mukofot"),
                T("Ball: {score} / {max_score} ({percent}%)", 25, 69.5, 50, 5, 1.6, "#4b5563", "lora"),
                T("{date}", 15, 85, 25, 4, 1.3, "#4b5563", "lora"),
                T("ID: {code}", 60, 85, 25, 4, 1.3, "#6b7280", "lora"),
            ])}


def minimal() -> dict:
    return {"page": _page(), "bg": {"type": "color", "color": "#ffffff"}, "els": _named([
        L(8, 10, 84, "#111827", 0.12), L(8, 90, 84, "#111827", 0.12),
        T("SERTIFIKAT", 10, 16, 80, 10, 4.2, "#111827", "raleway", False, ls=0.35, name="Sarlavha"),
        T("{olympiad}", 15, 27, 70, 5, 1.7, "#6b7280", "raleway"),
        T("{full_name}", 10, 39, 80, 12, 4.6, "#111827", "raleway", True, wrap=False, name="Familiya Ism"),
        T("{school}, {grade}-sinf", 15, 53, 70, 5, 1.7, "#4b5563", "raleway"),
        T("{subject} — {award}", 12, 60, 76, 6, 2.2, "#111827", "raleway", True, name="Mukofot"),
        T("{score} / {max_score} ball · {percent}%", 25, 67, 50, 5, 1.5, "#6b7280", "raleway"),
        T("{date}", 8, 82, 30, 4, 1.25, "#6b7280", "raleway", align="left"),
        T("ID {code}", 62, 82, 30, 4, 1.25, "#9ca3af", "raleway", align="right"),
    ])}


def _diploma(place: int, c1: str, c2: str, title: str) -> dict:
    return {"page": _page(), "bg": {"type": "gradient", "color": "#0f172a", "color2": "#1e293b", "angle": 135},
            "els": _named([
                R(2.2, 3.1, 95.6, 93.8, stroke=c1, sw=0.45),
                R(3.4, 4.8, 93.2, 90.4, stroke=c2, sw=0.1),
                E(43, 7, 14, 19.8, fill=c1, stroke=c2, sw=0.4, name="Medal"),
                T(str(place), 43, 7, 14, 19.8, 6.5, "#0f172a", "oswald", True, name="Medal raqami"),
                T("DIPLOM", 15, 28, 70, 11, 5.8, c1, "cormorant", True, ls=0.25, name="Sarlavha"),
                T(title, 20, 39, 60, 5, 2.0, c2, "montserrat", True, ls=0.2, name="Daraja"),
                T("{olympiad}", 15, 45, 70, 5, 1.7, "#cbd5e1", "montserrat"),
                T("{full_name}", 10, 51, 80, 12, 4.6, "#ffffff", "cormorant", True, wrap=False, name="Familiya Ism"),
                T("{school}, {grade}-sinf o'quvchisi", 15, 64, 70, 5, 1.7, "#cbd5e1", "montserrat"),
                T("{subject} fanidan {place_text}", 12, 70, 76, 6, 2.4, c1, "cormorant", True, name="Mukofot"),
                T("Ball: {score} / {max_score} ({percent}%)", 25, 76.5, 50, 5, 1.5, "#94a3b8", "montserrat"),
                T("{date}", 10, 87, 25, 4, 1.25, "#94a3b8", "montserrat"),
                T("ID: {code}", 65, 87, 25, 4, 1.25, "#64748b", "montserrat"),
            ])}


def diploma_1() -> dict:
    return _diploma(1, "#f5c542", "#fde68a", "I DARAJALI")


def diploma_2() -> dict:
    return _diploma(2, "#d1d5db", "#f3f4f6", "II DARAJALI")


def diploma_3() -> dict:
    return _diploma(3, "#d4915a", "#f3c79f", "III DARAJALI")


def blank() -> dict:
    """O'z rasmingiz uchun: Canva'da tayyorlangan fonni yuklang va faqat ismni joylashtiring."""
    return {"page": _page(), "bg": {"type": "color", "color": "#ffffff"}, "els": _named([
        T("{full_name}", 10, 44, 80, 12, 4.6, "#111827", "cormorant", True, wrap=False, name="Familiya Ism"),
    ])}


# ---------------------------------------------------------------------------
# Ruxsatnoma
# ---------------------------------------------------------------------------
def pass_default() -> dict:
    navy, blue = "#1e3a8a", "#2563eb"
    rows = [("Sana", "{exam_date}, {weekday}"), ("Kelish vaqti", "{arrive_time}"),
            ("Boshlanish", "{start_time} ({session})"), ("Bino", "{building}"),
            ("Manzil", "{address}"), ("Xona", "{room}"), ("O'rin", "{seat_text}"), ("Fanlar", "{subjects}")]
    els = [
        R(0, 0, 100, 16, fill=navy, name="Sarlavha fon"),
        E(78, -6, 34, 24, fill="#ffffff", opacity=0.08),
        T("RUXSATNOMA", 6, 2.5, 88, 7, 7.0, "#ffffff", "oswald", True, ls=0.12, name="Sarlavha"),
        T("{olympiad} · {round}", 6, 9.5, 88, 5, 2.6, "#bfdbfe", "montserrat", name="Olimpiada nomi"),
        R(6, 18.5, 88, 15, fill="#eff6ff", stroke="#bfdbfe", sw=0.25, radius=10),
        T("{full_name}", 9, 19.5, 60, 7, 5.2, "#0f172a", "montserrat", True, "left", wrap=False, name="Familiya Ism"),
        T("{school}, {grade}-sinf", 9, 26.5, 60, 6, 3.0, "#475569", "montserrat", align="left"),
        T("ID", 70, 19.8, 21, 3, 2.4, "#64748b", "montserrat", True),
        T("{code}", 70, 22.5, 21, 8, 8.5, blue, "oswald", True, wrap=False, name="ID"),
    ]
    y = 36.0
    for label, value in rows:
        els.append(T(label, 8, y, 25, 4.6, 2.9, "#64748b", "montserrat", align="left"))
        els.append(T(value, 33, y, 59, 4.6, 3.2, "#0f172a", "montserrat", True, "left", name=label))
        els.append(L(8, y + 5.0, 84, "#e5e7eb", 0.15))
        y += 5.4
    els += [
        R(6, y + 1.2, 88, 11.5, fill="#fef3c7", stroke="#fcd34d", sw=0.2, radius=8),
        T("Eslatma: ruchka va shaxsingizni tasdiqlovchi hujjat (metrika yoki ID karta) olib keling. "
          "Kelish vaqtidan kechikmang. Telefon va qo'shimcha qog'oz bilan kirish mumkin emas.",
          9, y + 2.0, 82, 10, 2.6, "#78350f", "montserrat", align="left", name="Eslatma"),
        T("Ruxsatnomani telefoningizda yoki chop etib olib keling", 6, 95, 88, 3.5, 2.2, "#94a3b8", "montserrat"),
    ]
    return {"page": _page("A5P"), "bg": {"type": "color", "color": "#ffffff"}, "els": _named(els)}


CERT_PRESETS: dict[str, tuple[str, Callable[[], dict]]] = {
    "classic_gold": ("Klassik oltin", classic_gold),
    "modern_blue": ("Zamonaviy ko'k", modern_blue),
    "green_nature": ("Yashil", green_nature),
    "minimal": ("Minimal", minimal),
    "diploma_1": ("Diplom — 1-o'rin (oltin)", diploma_1),
    "diploma_2": ("Diplom — 2-o'rin (kumush)", diploma_2),
    "diploma_3": ("Diplom — 3-o'rin (bronza)", diploma_3),
    "blank": ("Bo'sh — o'z rasmingiz bilan", blank),
}
PASS_PRESETS: dict[str, tuple[str, Callable[[], dict]]] = {
    "pass_default": ("Ruxsatnoma (A5)", pass_default),
}


def build(key: str) -> dict:
    """Tayyor shablonni tekshirilgan dizayn ko'rinishida qaytaradi."""
    presets = {**CERT_PRESETS, **PASS_PRESETS}
    _, fn = presets.get(key) or presets["classic_gold"]
    return dz.clean_design(fn())

"""Dizayn tizimi: sertifikat, diplom va ruxsatnomalar uchun (Canva'ga o'xshash).

Dizayn — JSON hujjat:

    {
      "v": 2,
      "page": {"preset": "A4L", "dpi": 150, "w": 1754, "h": 1240},
      "bg":   {"type": "color" | "gradient" | "image", "color": "#ffffff", "color2": "#dbeafe",
               "angle": 135, "image": "<asset>", "fit": "cover" | "contain" | "stretch"},
      "els":  [ element, ... ]          # pastdan yuqoriga (oxirgisi eng ustida)
    }

Element umumiy maydonlari: id, type (text|image|rect|ellipse|line), name, x, y, w, h,
rot (gradus, soat mili bo'yicha), opacity (0..1), locked, hidden.
  * x, w — sahifa kengligining foizi; y, h — balandligining foizi (chap-yuqori burchakdan).
  * Barcha qalinlik va o'lchamlar (shrift, chiziq, soya) — sahifa KENGLIGINING foizi.
    Shu sababli dizayn istalgan o'lcham/sifatda bir xil ko'rinadi.

Matn: text, font, bold, size, color, align (left|center|right), valign (top|middle|bottom),
      lh (qator balandligi), ls (harflar oralig'i, em), upper, wrap (so'z bo'yicha ko'chirish),
      fit (sig'masa kichrayadi), shadow {color, blur, dx, dy} | null, stroke {color, w} | null.
Rasm: asset, fit (contain|cover|stretch), radius.
Shakl (rect/ellipse): fill ("" — bo'sh), stroke, sw, radius (faqat rect, 0..100).
Chiziq (line): stroke, sw — to'rtburchak markazidan gorizontal chiziq (burilsa — qiya).

MUHIM: matnni joylashtirish (o'lchash, qatorlarga bo'lish, kichraytirish) brauzer muharririda
ham AYNAN shu algoritm va shu harf kengliklari bilan bajariladi (font_metrics_payload) —
ekranda ko'rgan narsangiz PDF'da ham o'sha joyda chiqadi.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageMath

from ..config import config

STATIC_FONTS = Path(__file__).resolve().parent.parent / "web" / "static" / "fonts"
ASSET_DIR_NAME = "certificates/assets"
MAX_SIDE = 3508  # A4 300 dpi — bundan katta rasm kerak emas

# ---------------------------------------------------------------------------
# Shriftlar
# ---------------------------------------------------------------------------
# kalit: (ko'rinadigan nom, oddiy fayl, qalin fayl yoki None, turkum)
BUILTIN_FONTS: dict[str, tuple[str, str, Optional[str], str]] = {
    "dejavu-sans": ("DejaVu Sans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "Oddiy"),
    "montserrat": ("Montserrat", "Montserrat-Regular.ttf", "Montserrat-Bold.ttf", "Oddiy"),
    "raleway": ("Raleway", "Raleway-Regular.ttf", "Raleway-Bold.ttf", "Oddiy"),
    "opensans": ("Open Sans", "OpenSans-Regular.ttf", "OpenSans-Bold.ttf", "Oddiy"),
    "nunito": ("Nunito", "Nunito-Regular.ttf", "Nunito-Bold.ttf", "Oddiy"),
    "oswald": ("Oswald", "Oswald-Regular.ttf", "Oswald-Bold.ttf", "Sarlavha"),
    "dejavu-serif": ("DejaVu Serif", "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "Klassik"),
    "ptserif": ("PT Serif", "PTSerif-Regular.ttf", "PTSerif-Bold.ttf", "Klassik"),
    "lora": ("Lora", "Lora-Regular.ttf", "Lora-Bold.ttf", "Klassik"),
    "ebgaramond": ("EB Garamond", "EBGaramond-Regular.ttf", "EBGaramond-Bold.ttf", "Klassik"),
    "cormorant": ("Cormorant Garamond", "CormorantGaramond-Regular.ttf", "CormorantGaramond-Bold.ttf", "Klassik"),
    "forum": ("Forum", "Forum-Regular.ttf", None, "Klassik"),
    "pacifico": ("Pacifico", "Pacifico-Regular.ttf", None, "Yozma"),
    "badscript": ("Bad Script", "BadScript-Regular.ttf", None, "Yozma"),
    "caveat": ("Caveat", "Caveat-Regular.ttf", "Caveat-Bold.ttf", "Yozma"),
}
FALLBACK_FONT = "dejavu-sans"
# Eski (1-versiya) shrift nomlari → (oila, qalin)
LEGACY_FONTS = {"sans": ("dejavu-sans", False), "sans-bold": ("dejavu-sans", True),
                "serif": ("dejavu-serif", False), "serif-bold": ("dejavu-serif", True)}

# Shriftda yo'q belgilar o'rniga (birinchi mavjudi olinadi)
REPLACEMENTS = {
    "ʻ": "‘'", "ʼ": "’'", "‘": "'", "’": "'", "`": "'", "“": '"', "”": '"', "«": '"', "»": '"',
    "—": "-", "–": "-", "№": "N", "…": ".", " ": " ",
}


def _custom_font_dir() -> Path:
    return config.UPLOAD_DIR / "fonts"


def custom_fonts() -> dict[str, tuple[str, str, Optional[str], str]]:
    """Admin yuklagan shriftlar (settings: custom_fonts — JSON ro'yxat)."""
    from . import settings as st
    try:
        items = json.loads(st.raw("custom_fonts") or "[]")
    except ValueError:
        items = []
    out = {}
    for it in items if isinstance(items, list) else []:
        try:
            key, name, reg = str(it["key"]), str(it["name"]), str(it["regular"])
        except (KeyError, TypeError):
            continue
        bold = it.get("bold") or None
        if (_custom_font_dir() / reg).exists():
            out[key] = (name, reg, bold if bold and (_custom_font_dir() / bold).exists() else None, "Yuklangan")
    return out


def all_fonts() -> dict[str, tuple[str, str, Optional[str], str]]:
    return {**BUILTIN_FONTS, **custom_fonts()}


def font_path(family: str, bold: bool) -> Path:
    """Oila va qalinlik bo'yicha shrift fayli (qalini bo'lmasa — oddiysi)."""
    fonts = all_fonts()
    info = fonts.get(family) or fonts[FALLBACK_FONT]
    base = STATIC_FONTS if family in BUILTIN_FONTS or family not in fonts else _custom_font_dir()
    fname = info[2] if bold and info[2] else info[1]
    return base / fname


@dataclass
class FontMetrics:
    """Shrift o'lchamlari em birligida (unitsPerEm = 1): bu qiymatlar brauzerga ham yuboriladi."""
    asc: float
    desc: float
    adv: dict[int, float]  # belgi kodi → kenglik (em)


_metrics_lock = threading.Lock()


@lru_cache(maxsize=64)
def _metrics_cached(path: str, mtime: float) -> FontMetrics:
    from fontTools.ttLib import TTFont
    with _metrics_lock:
        f = TTFont(path, lazy=True)
        upem = f["head"].unitsPerEm
        cmap = f.getBestCmap() or {}
        hmtx = f["hmtx"]
        adv = {}
        for code, glyph in cmap.items():
            try:
                # 5 xonagacha yaxlitlaymiz — brauzerga ham aynan shu qiymat boradi (hisob bir xil chiqadi)
                adv[code] = round(hmtx[glyph][0] / upem, 5)
            except KeyError:
                continue
        hhea = f["hhea"]
        asc, desc = round(hhea.ascent / upem, 5), round(abs(hhea.descent) / upem, 5)
        if asc + desc <= 0:  # buzuq shrift — xavfsiz qiymat
            asc, desc = 0.8, 0.2
        f.close()
    return FontMetrics(asc=asc, desc=desc, adv=adv)


def metrics(path: Path) -> FontMetrics:
    return _metrics_cached(str(path), path.stat().st_mtime)


@lru_cache(maxsize=256)
def _pil_font(path: str, size: float, mtime: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, max(size, 1.0), layout_engine=ImageFont.Layout.BASIC)


def pil_font(path: Path, size: float) -> ImageFont.FreeTypeFont:
    # O'lchamni 0.25 px aniqlikda yaxlitlaymiz — kesh samarali ishlashi uchun
    return _pil_font(str(path), round(size * 4) / 4, path.stat().st_mtime)


# Brauzerga yuboriladigan belgilar to'plami (subset qilingan diapazonlar bilan bir xil)
CHARSET = (list(range(0x20, 0x7F)) + list(range(0xA0, 0x180)) + list(range(0x2B0, 0x300))
           + list(range(0x400, 0x530)) + list(range(0x2000, 0x2070)) + [0x2116, 0x20AC, 0x2122, 0x2022, 0x25CF])


CHARSET_SET = frozenset(CHARSET)


def font_metrics_payload() -> dict:
    """Muharrir uchun: har bir shrift fayli bo'yicha asc/desc va CHARSET'dagi belgilar kengligi."""
    out = {"charset": CHARSET, "fonts": {}}
    for key, (name, reg, bold, cat) in all_fonts().items():
        entry = {"name": name, "cat": cat, "has_bold": bool(bold), "files": {}}
        for variant, is_bold in (("r", False), ("b", True)):
            if is_bold and not bold:
                continue
            p = font_path(key, is_bold)
            m = metrics(p)
            url = (f"/static/fonts/{p.name}" if key in BUILTIN_FONTS
                   else f"/admin/certificates/fontfile/{p.name}")
            entry["files"][variant] = {
                "url": url, "asc": m.asc, "desc": m.desc,
                "adv": [m.adv.get(c, -1) for c in CHARSET],  # -1 — shriftda bu belgi yo'q
            }
        out["fonts"][key] = entry
    return out


# ---------------------------------------------------------------------------
# Sahifa o'lchamlari
# ---------------------------------------------------------------------------
PAGE_PRESETS = {  # mm (kenglik, balandlik)
    "A4L": ("A4 albom", 297, 210), "A4P": ("A4 portret", 210, 297),
    "A5L": ("A5 albom", 210, 148), "A5P": ("A5 portret", 148, 210),
}
DPIS = (150, 200, 300)


def page_size(page: dict) -> tuple[int, int]:
    """Dizayn sahifasining piksel o'lchami."""
    preset = page.get("preset", "A4L")
    dpi = page.get("dpi", 150) if page.get("dpi") in DPIS else 150  # "image" uchun dpi o'lchamga ta'sir qilmaydi
    if preset in PAGE_PRESETS:
        _, wmm, hmm = PAGE_PRESETS[preset]
        return round(wmm / 25.4 * dpi), round(hmm / 25.4 * dpi)
    if preset == "custom":
        wmm = _num(page.get("w_mm"), 297, 50, 1000)
        hmm = _num(page.get("h_mm"), 210, 50, 1000)
        w, h = round(wmm / 25.4 * dpi), round(hmm / 25.4 * dpi)
        k = min(1.0, MAX_SIDE / max(w, h))
        return max(100, round(w * k)), max(100, round(h * k))
    # "image": fon rasmining o'lchami (saqlashda w/h yoziladi)
    w, h = int(_num(page.get("w"), 1754, 100, MAX_SIDE)), int(_num(page.get("h"), 1240, 100, MAX_SIDE))
    return w, h


# ---------------------------------------------------------------------------
# Dizaynni tekshirish (brauzerdan kelgan ma'lumot — ishonchsiz)
# ---------------------------------------------------------------------------
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_ASSET = re.compile(r"^[a-f0-9]{16}\.png$")
EL_TYPES = ("text", "image", "rect", "ellipse", "line")
MAX_ELEMENTS = 120


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return max(lo, min(hi, x))


def _color(v: Any, default: str = "#000000", allow_empty: bool = False) -> str:
    v = str(v or "").strip()
    if allow_empty and v == "":
        return ""
    return v if _COLOR.match(v) else default


def _asset(v: Any) -> str:
    v = str(v or "")
    return v if _ASSET.match(v) else ""


def clean_design(raw: Any) -> dict:
    """Dizaynni tekshiradi va standart qiymatlar bilan to'ldiradi. Xato bo'lsa ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("Dizayn noto'g'ri")
    page_in = raw.get("page") if isinstance(raw.get("page"), dict) else {}
    preset = page_in.get("preset") if page_in.get("preset") in (*PAGE_PRESETS, "custom", "image") else "A4L"
    page = {"preset": preset, "dpi": page_in.get("dpi") if page_in.get("dpi") in DPIS else 150}
    if preset == "image":  # fon rasmi o'lchamidagi sahifa: chop etish o'lchami uchun erkin DPI
        page["dpi"] = int(_num(page_in.get("dpi"), 150, 50, 600))
    if preset == "custom":
        page["w_mm"] = _num(page_in.get("w_mm"), 297, 50, 1000)
        page["h_mm"] = _num(page_in.get("h_mm"), 210, 50, 1000)
    if preset == "image":
        page["w"] = int(_num(page_in.get("w"), 1754, 100, MAX_SIDE))
        page["h"] = int(_num(page_in.get("h"), 1240, 100, MAX_SIDE))
    page["w"], page["h"] = page_size(page)

    bg_in = raw.get("bg") if isinstance(raw.get("bg"), dict) else {}
    bg = {"type": bg_in.get("type") if bg_in.get("type") in ("color", "gradient", "image") else "color",
          "color": _color(bg_in.get("color"), "#ffffff"),
          "color2": _color(bg_in.get("color2"), "#dbeafe"),
          "angle": _num(bg_in.get("angle"), 135, -360, 360),
          "image": _asset(bg_in.get("image")),
          "fit": bg_in.get("fit") if bg_in.get("fit") in ("cover", "contain", "stretch") else "cover"}
    if bg["type"] == "image" and not bg["image"]:
        bg["type"] = "color"

    els = []
    fonts = all_fonts()
    seen_ids: set[str] = set()
    for e in (raw.get("els") or [])[:MAX_ELEMENTS]:
        if not isinstance(e, dict) or e.get("type") not in EL_TYPES:
            continue
        eid = re.sub(r"[^A-Za-z0-9_-]", "", str(e.get("id") or ""))[:24] or f"e{len(els) + 1}"
        while eid in seen_ids:
            eid += "x"
        seen_ids.add(eid)
        out = {"id": eid, "type": e["type"], "name": str(e.get("name") or "")[:60],
               "x": _num(e.get("x"), 10, -200, 300), "y": _num(e.get("y"), 10, -200, 300),
               "w": _num(e.get("w"), 30, 0.2, 500), "h": _num(e.get("h"), 10, 0.2, 500),
               "rot": _num(e.get("rot"), 0, -360, 360), "opacity": _num(e.get("opacity"), 1, 0, 1),
               "locked": bool(e.get("locked")), "hidden": bool(e.get("hidden"))}
        t = e["type"]
        if t == "text":
            sh, sk = e.get("shadow"), e.get("stroke")
            out.update({
                "text": str(e.get("text") or "")[:2000].replace("\r", ""),
                "font": e.get("font") if e.get("font") in fonts else FALLBACK_FONT,
                "bold": bool(e.get("bold")), "italic": False,
                "size": _num(e.get("size"), 3, 0.2, 40),
                "color": _color(e.get("color"), "#111827"),
                "align": e.get("align") if e.get("align") in ("left", "center", "right") else "center",
                "valign": e.get("valign") if e.get("valign") in ("top", "middle", "bottom") else "middle",
                "lh": _num(e.get("lh"), 1.2, 0.6, 4), "ls": _num(e.get("ls"), 0, -0.2, 2),
                "upper": bool(e.get("upper")), "wrap": bool(e.get("wrap", True)), "fit": bool(e.get("fit", True)),
                "shadow": ({"color": _color(sh.get("color"), "#00000066"), "blur": _num(sh.get("blur"), 0.3, 0, 5),
                            "dx": _num(sh.get("dx"), 0.15, -5, 5), "dy": _num(sh.get("dy"), 0.15, -5, 5)}
                           if isinstance(sh, dict) else None),
                "stroke": ({"color": _color(sk.get("color"), "#ffffff"), "w": _num(sk.get("w"), 0.1, 0.01, 3)}
                           if isinstance(sk, dict) else None),
            })
        elif t == "image":
            out.update({"asset": _asset(e.get("asset")),
                        "fit": e.get("fit") if e.get("fit") in ("contain", "cover", "stretch") else "contain",
                        "radius": _num(e.get("radius"), 0, 0, 100)})
            if not out["asset"]:
                continue
        else:
            out.update({"fill": _color(e.get("fill"), "" if t == "line" else "#e5e7eb", allow_empty=True),
                        "stroke": _color(e.get("stroke"), "", allow_empty=True),
                        "sw": _num(e.get("sw"), 0.2, 0, 10)})
            if t == "rect":
                out["radius"] = _num(e.get("radius"), 0, 0, 100)
            if t == "line":
                out["fill"] = ""
                out["stroke"] = out["stroke"] or "#111827"
                out["sw"] = max(out["sw"], 0.02)
        els.append(out)
    return {"v": 2, "page": page, "bg": bg, "els": els}


def assets_in(design: dict) -> set[str]:
    names = {design.get("bg", {}).get("image", "")}
    names |= {e.get("asset", "") for e in design.get("els", []) if e.get("type") == "image"}
    return {n for n in names if n}


# ---------------------------------------------------------------------------
# Eski (1-versiya) shablonni yangi dizaynga o'girish
# ---------------------------------------------------------------------------
def from_legacy(fields: list[dict], background_path: str, width: int, height: int) -> dict:
    """Eski shablon: fon rasmi + {x, y — langar nuqtasi, size, max_width} maydonlar."""
    W, H = max(width, 100), max(height, 100)
    els = []
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            continue
        family, bold = LEGACY_FONTS.get(f.get("font"), (FALLBACK_FONT, False))
        x, y = _num(f.get("x"), 50, 0, 100), _num(f.get("y"), 50, 0, 100)
        size = _num(f.get("size"), 3, 0.3, 20)
        align = f.get("align") if f.get("align") in ("left", "center", "right") else "center"
        mw = _num(f.get("max_width"), 0, 0, 100)
        if mw <= 0:  # cheklanmagan edi — sahifa chetigacha
            mw = {"center": 2 * min(x, 100 - x), "left": 100 - x, "right": x}[align] or 40
        left = {"center": x - mw / 2, "left": x, "right": x - mw}[align]
        h_pct = size * W / 100 * 1.6 / H * 100  # bitta qator balandligi + zaxira
        els.append({"id": f"t{i + 1}", "type": "text", "text": str(f.get("text", "")), "font": family,
                    "bold": bold, "size": size, "color": f.get("color", "#000000"), "align": align,
                    "valign": "middle", "lh": 1.2, "ls": 0, "upper": False, "wrap": False, "fit": True,
                    "x": left, "y": y - h_pct / 2, "w": mw, "h": h_pct, "rot": 0, "opacity": 1})
    design = {"page": {"preset": "image", "w": W, "h": H, "dpi": 150},
              "bg": {"type": "color", "color": "#ffffff"}, "els": els}
    if background_path and Path(background_path).exists():
        try:
            design["bg"] = {"type": "image", "image": import_asset(Path(background_path).read_bytes()),
                            "fit": "stretch", "color": "#ffffff"}
        except Exception:
            pass
    return clean_design(design)


# ---------------------------------------------------------------------------
# Rasmlar (fon, logo, muhr, imzo)
# ---------------------------------------------------------------------------
def asset_dir() -> Path:
    d = config.UPLOAD_DIR / "certificates" / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def asset_path(name: str) -> Optional[Path]:
    if not _ASSET.match(name or ""):
        return None
    p = asset_dir() / name
    return p if p.exists() else None


def import_asset(data: bytes) -> str:
    """Yuklangan rasmni tekshiradi, PNG ga o'giradi va saqlaydi. Fayl nomini qaytaradi.

    Bir xil rasm qayta yuklansa, nusxa yaratilmaydi (nom — mazmun xeshi).
    """
    Image.MAX_IMAGE_PIXELS = 60_000_000  # «decompression bomb»dan himoya
    img = Image.open(BytesIO(data))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if ("transparency" in img.info or img.mode in ("LA", "PA", "P")) else "RGB")
    if max(img.size) > MAX_SIDE:
        k = MAX_SIDE / max(img.size)
        img = img.resize((max(1, int(img.width * k)), max(1, int(img.height * k))), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, "PNG", optimize=True)
    png = buf.getvalue()
    name = hashlib.sha1(png).hexdigest()[:16] + ".png"
    path = asset_dir() / name
    if not path.exists():
        path.write_bytes(png)
    return name


def asset_size(name: str) -> tuple[int, int]:
    p = asset_path(name)
    if p is None:
        return 0, 0
    with Image.open(p) as im:
        return im.size


@lru_cache(maxsize=32)
def _load_asset(path: str, mtime: float) -> Image.Image:
    img = Image.open(path)
    img.load()
    return img.convert("RGBA")


def load_asset(name: str) -> Optional[Image.Image]:
    p = asset_path(name)
    return _load_asset(str(p), p.stat().st_mtime) if p else None


# ---------------------------------------------------------------------------
# Matnni joylashtirish (brauzerdagi muharrir bilan bir xil algoritm!)
# ---------------------------------------------------------------------------
_PH = re.compile(r"\{([a-z_]+)\}")


def fill(text: str, ctx: dict) -> str:
    """{placeholder}larni qiymat bilan almashtiradi (noma'lumi o'zgarishsiz qoladi)."""
    return _PH.sub(lambda m: str(ctx[m.group(1)]) if m.group(1) in ctx else m.group(0), text)


def is_dynamic(el: dict) -> bool:
    return el.get("type") == "text" and bool(_PH.search(el.get("text", "")))


def prepare_text(text: str, family: str, bold: bool) -> tuple[str, Path, FontMetrics]:
    """Shriftda yo'q belgilarni almashtiradi; baribir yo'q bo'lsa — DejaVu Sans (u ham bo'lmasa — tashlab yuboriladi)."""
    path = font_path(family, bold)
    m = metrics(path)

    def has(c: str) -> bool:
        # Faqat CHARSET'dagi belgilar — brauzer ham aynan shularni biladi (natija bir xil bo'lsin)
        return ord(c) in m.adv and ord(c) in CHARSET_SET

    out, missing = [], False
    for ch in text:
        if ch == "\n" or has(ch):
            out.append(ch)
            continue
        rep = next((r for r in REPLACEMENTS.get(ch, "") if has(r)), None)
        if rep is not None:
            out.append(rep)
        else:
            out.append(ch)
            missing = True
    text = "".join(out)
    if missing and family != FALLBACK_FONT:
        return prepare_text(text, FALLBACK_FONT, bold)
    if missing:  # DejaVu'da ham yo'q (masalan, emoji)
        text = "".join(ch for ch in text if ch == "\n" or has(ch))
    return text, path, m


def text_width(s: str, m: FontMetrics, size: float, ls: float) -> float:
    """Qator kengligi (px): harflar kengligi + har bir harfdan keyin oraliq (CSS letter-spacing kabi)."""
    return sum(m.adv.get(ord(c), 0.0) for c in s) * size + ls * size * len(s)


def wrap_lines(text: str, m: FontMetrics, size: float, ls: float, max_w: float, wrap: bool) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        if not wrap:
            lines.append(para)
            continue
        words = para.split(" ")
        line = words[0]
        for w in words[1:]:
            cand = line + " " + w
            if text_width(cand, m, size, ls) <= max_w:
                line = cand
            else:
                lines.append(line)
                line = w
        lines.append(line)
    return lines


@dataclass
class TextLayout:
    lines: list[str]
    size: float          # yakuniy shrift o'lchami (px)
    path: Path
    m: FontMetrics
    xs: list[float]      # har qatorning chap chekkasi (box ichida, px)
    baselines: list[float]


def layout_text(el: dict, ctx: dict, W: int, H: int) -> TextLayout:
    raw = fill(el.get("text", ""), ctx)
    if el.get("upper"):
        raw = raw.upper()
    text, path, m = prepare_text(raw, el.get("font", FALLBACK_FONT), bool(el.get("bold")))
    bw, bh = el["w"] * W / 100, el["h"] * H / 100
    size = el["size"] * W / 100
    min_size = max(4.0, size * 0.2)
    ls, lh, wrap = el.get("ls", 0), el.get("lh", 1.2), el.get("wrap", True)
    while True:
        lines = wrap_lines(text, m, size, ls, bw, wrap)
        widest = max((text_width(s, m, size, ls) for s in lines), default=0)
        block_h = len(lines) * lh * size
        if not el.get("fit", True) or size <= min_size:
            break
        if widest <= bw + 0.5 and (not wrap or block_h <= bh + 0.5):
            break
        size = max(min_size, size * 0.95)
    L = lh * size
    block_h = len(lines) * L
    top = {"top": 0.0, "bottom": bh - block_h}.get(el.get("valign", "middle"), (bh - block_h) / 2)
    xs, bases = [], []
    for i, s in enumerate(lines):
        w = text_width(s, m, size, ls)
        xs.append({"left": 0.0, "right": bw - w}.get(el.get("align", "center"), (bw - w) / 2))
        bases.append(top + i * L + (L - (m.asc + m.desc) * size) / 2 + m.asc * size)
    return TextLayout(lines, size, path, m, xs, bases)


# ---------------------------------------------------------------------------
# Chizish (Pillow)
# ---------------------------------------------------------------------------
def _rgba(color: str, opacity: float = 1.0) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    a = int(c[6:8], 16) if len(c) == 8 else 255
    return r, g, b, int(round(a * opacity))


def _gradient(W: int, H: int, c1: str, c2: str, angle: float) -> Image.Image:
    """CSS/SVG linear-gradient(angle) bilan bir xil: 0° — pastdan tepaga, 90° — chapdan o'ngga."""
    a = math.radians(angle)
    dx, dy = math.sin(a), -math.cos(a)
    L = abs(W * dx) + abs(H * dy) or 1.0
    xs = Image.new("F", (W, 1))
    xs.putdata([float(i) + 0.5 for i in range(W)])
    ys = Image.new("F", (1, H))
    ys.putdata([float(i) + 0.5 for i in range(H)])
    X, Y = xs.resize((W, H), Image.NEAREST), ys.resize((W, H), Image.NEAREST)
    k, b = 255.0 / L, 127.5 - 255.0 / L * (W / 2 * dx + H / 2 * dy)
    t = ImageMath.lambda_eval(lambda e: e["X"] * (dx * k) + e["Y"] * (dy * k) + b, X=X, Y=Y)
    mask = t.convert("L")  # 0..255 oralig'iga qirqiladi
    return Image.composite(Image.new("RGBA", (W, H), _rgba(c2)), Image.new("RGBA", (W, H), _rgba(c1)), mask)


def _fit_image(img: Image.Image, w: int, h: int, fit: str) -> tuple[Image.Image, int, int]:
    """Rasmni (w×h) qutiga joylash: (rasm, x, y) — quti ichidagi o'rni."""
    w, h = max(1, w), max(1, h)
    if fit == "stretch":
        return img.resize((w, h), Image.LANCZOS), 0, 0
    k = (max if fit == "cover" else min)(w / img.width, h / img.height)
    nw, nh = max(1, round(img.width * k)), max(1, round(img.height * k))
    im = img.resize((nw, nh), Image.LANCZOS)
    x, y = (w - nw) // 2, (h - nh) // 2
    if fit == "cover":
        im = im.crop((-x, -y, -x + w, -y + h))
        return im, 0, 0
    return im, x, y


def _background(design: dict, W: int, H: int) -> Image.Image:
    bg = design.get("bg", {})
    base = Image.new("RGBA", (W, H), _rgba(bg.get("color", "#ffffff")))
    if bg.get("type") == "gradient":
        return _gradient(W, H, bg.get("color", "#ffffff"), bg.get("color2", "#dbeafe"), bg.get("angle", 135))
    if bg.get("type") == "image":
        img = load_asset(bg.get("image", ""))
        if img is not None:
            im, x, y = _fit_image(img, W, H, bg.get("fit", "cover"))
            base.alpha_composite(im, (x, y))
    return base


def _with_opacity(tile: Image.Image, opacity: float) -> Image.Image:
    if opacity >= 0.999:
        return tile
    a = tile.getchannel("A").point(lambda v: int(v * opacity))
    tile.putalpha(a)
    return tile


def _place(canvas: Image.Image, tile: Image.Image, cx: float, cy: float, rot: float) -> None:
    """Tayl markazini (cx, cy) ga qo'yadi, kerak bo'lsa burab."""
    if abs(rot) > 0.01:
        tile = tile.rotate(-rot, resample=Image.BICUBIC, expand=True)
    x, y = round(cx - tile.width / 2), round(cy - tile.height / 2)
    # Sahifadan chiqib ketgan qismni qirqamiz (alpha_composite manfiy koordinatani qabul qilmaydi)
    sx, sy = max(0, -x), max(0, -y)
    ex, ey = min(tile.width, canvas.width - x), min(tile.height, canvas.height - y)
    if ex <= sx or ey <= sy:
        return
    canvas.alpha_composite(tile.crop((sx, sy, ex, ey)), (x + sx, y + sy))


def _draw_text(canvas: Image.Image, el: dict, ctx: dict, W: int, H: int) -> None:
    lay = layout_text(el, ctx, W, H)
    if not any(s.strip() for s in lay.lines):
        return
    bx, by = el["x"] * W / 100, el["y"] * H / 100
    bw, bh = el["w"] * W / 100, el["h"] * H / 100
    font = pil_font(lay.path, lay.size)
    ls_px = el.get("ls", 0) * lay.size
    stroke = el.get("stroke")
    sw = stroke["w"] * W / 100 if stroke else 0
    shadow = el.get("shadow")
    # Tayl: quti va matn chegaralarini (soya, kontur bilan) qamraydi; markazi — quti markazi
    pad = sw + lay.size * 0.6
    if shadow:
        pad += (abs(shadow["dx"]) + abs(shadow["dy"]) + shadow["blur"] * 3) * W / 100
    left = min([0.0] + lay.xs)
    right = max([bw] + [x + text_width(s, lay.m, lay.size, el.get("ls", 0)) for x, s in zip(lay.xs, lay.lines)])
    top = min(0.0, lay.baselines[0] - lay.m.asc * lay.size)
    bottom = max(bh, lay.baselines[-1] + lay.m.desc * lay.size)
    hx = max(bw / 2 - left, right - bw / 2) + pad
    hy = max(bh / 2 - top, bottom - bh / 2) + pad
    tw, th = int(math.ceil(2 * hx)), int(math.ceil(2 * hy))
    ox, oy = hx - bw / 2, hy - bh / 2  # quti chap-yuqori burchagining tayldagi o'rni

    def paint(layer: Image.Image, color: tuple, with_stroke: bool, dx: float = 0, dy: float = 0) -> None:
        """Har bir harfni hisoblangan aniq joyiga chizadi (brauzer muharriridagi bilan bir xil)."""
        d = ImageDraw.Draw(layer)
        for s, x0, base in zip(lay.lines, lay.xs, lay.baselines):
            x = ox + x0 + dx
            for ch in s:
                if not ch.isspace():
                    if with_stroke:
                        d.text((x, oy + base + dy), ch, font=font, fill=color, anchor="ls",
                               stroke_width=max(1, round(sw)), stroke_fill=color)
                    else:
                        d.text((x, oy + base + dy), ch, font=font, fill=color, anchor="ls")
                x += lay.m.adv.get(ord(ch), 0.0) * lay.size + ls_px

    tile = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        paint(sh, _rgba(shadow["color"]), bool(stroke), shadow["dx"] * W / 100, shadow["dy"] * W / 100)
        if shadow["blur"] > 0:
            sh = sh.filter(ImageFilter.GaussianBlur(shadow["blur"] * W / 100 / 2))
        tile.alpha_composite(sh)
    if stroke:  # avval kontur, ustidan asosiy rang (SVG paint-order: stroke)
        layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        paint(layer, _rgba(stroke["color"]), True)
        tile.alpha_composite(layer)
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    paint(layer, _rgba(el.get("color", "#000000")), False)
    tile.alpha_composite(layer)
    _place(canvas, _with_opacity(tile, el.get("opacity", 1)), bx + bw / 2, by + bh / 2, el.get("rot", 0))


def _round_mask(w: int, h: int, radius_px: float) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=max(0, radius_px), fill=255)
    return m


def _draw_shape(canvas: Image.Image, el: dict, W: int, H: int) -> None:
    bw, bh = el["w"] * W / 100, el["h"] * H / 100
    cx, cy = el["x"] * W / 100 + bw / 2, el["y"] * H / 100 + bh / 2
    sw = el.get("sw", 0) * W / 100
    t = el["type"]
    if t == "line":
        # Chiziq: qutining o'rta gorizontali, qalinligi sw
        w, h = max(1, round(bw)), max(1, round(sw))
        tile = Image.new("RGBA", (w, h), _rgba(el.get("stroke") or "#000000"))
    else:
        w, h = max(1, round(bw)), max(1, round(bh))
        # 4× kattaroq chizib kichraytiramiz — qirralari silliq bo'ladi
        ss = 4 if w * h < 2_500_000 else 2
        big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(big)
        fill_c = _rgba(el["fill"]) if el.get("fill") else None
        stroke_c = _rgba(el["stroke"]) if el.get("stroke") and sw > 0 else None
        swi = round(sw * ss) if stroke_c else 0
        box = [0, 0, w * ss - 1, h * ss - 1]
        if t == "rect":
            r = el.get("radius", 0) / 100 * min(w, h) / 2 * ss
            d.rounded_rectangle(box, radius=r, fill=fill_c, outline=stroke_c, width=swi)
        else:
            d.ellipse(box, fill=fill_c, outline=stroke_c, width=swi)
        tile = big.resize((w, h), Image.LANCZOS)
    _place(canvas, _with_opacity(tile, el.get("opacity", 1)), cx, cy, el.get("rot", 0))


def _draw_image(canvas: Image.Image, el: dict, W: int, H: int) -> None:
    img = load_asset(el.get("asset", ""))
    if img is None:
        return
    bw, bh = el["w"] * W / 100, el["h"] * H / 100
    w, h = max(1, round(bw)), max(1, round(bh))
    im, x, y = _fit_image(img, w, h, el.get("fit", "contain"))
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    tile.alpha_composite(im, (x, y))
    if el.get("radius", 0) > 0:
        r = el["radius"] / 100 * min(im.width, im.height) / 2
        mask = Image.new("L", (w, h), 0)
        mask.paste(_round_mask(im.width, im.height, r), (x, y))
        tile.putalpha(ImageChops.multiply(tile.getchannel("A"), mask))
    _place(canvas, _with_opacity(tile, el.get("opacity", 1)), el["x"] * W / 100 + bw / 2,
           el["y"] * H / 100 + bh / 2, el.get("rot", 0))


def _draw_element(canvas: Image.Image, el: dict, ctx: dict, W: int, H: int) -> None:
    if el.get("hidden"):
        return
    t = el.get("type")
    if t == "text":
        _draw_text(canvas, el, ctx, W, H)
    elif t == "image":
        _draw_image(canvas, el, W, H)
    elif t in ("rect", "ellipse", "line"):
        _draw_shape(canvas, el, W, H)


# Statik qism keshi: fon + birinchi o'zgaruvchan matngacha bo'lgan elementlar.
# 1500 ta sertifikatda fon va bezaklar bir marta chiziladi.
_base_cache: "OrderedDict[str, Image.Image]" = OrderedDict()
_base_lock = threading.Lock()


def _design_key(design: dict) -> str:
    raw = json.dumps(design, sort_keys=True, ensure_ascii=False)
    stamps = []
    for name in sorted(assets_in(design)):
        p = asset_path(name)
        stamps.append(f"{name}:{p.stat().st_mtime if p else 0}")
    return hashlib.sha1((raw + "|".join(stamps)).encode()).hexdigest()


def render(design: dict, ctx: dict) -> Image.Image:
    """Dizayn va ma'lumotdan RGB rasm yaratadi."""
    W, H = page_size(design.get("page", {}))
    els = design.get("els", [])
    split = next((i for i, e in enumerate(els) if is_dynamic(e) and not e.get("hidden")), len(els))
    key = _design_key(design)
    with _base_lock:
        base = _base_cache.get(key)
        if base is not None:
            _base_cache.move_to_end(key)
    if base is None:
        base = _background(design, W, H)
        for el in els[:split]:
            _draw_element(base, el, {}, W, H)
        with _base_lock:
            _base_cache[key] = base
            while len(_base_cache) > 8:
                _base_cache.popitem(last=False)
    img = base.copy()
    for el in els[split:]:
        _draw_element(img, el, ctx, W, H)
    return img.convert("RGB")


def to_pdf(img: Image.Image, dpi: int = 150) -> bytes:
    buf = BytesIO()
    img.save(buf, "PDF", resolution=float(dpi), quality=92)
    return buf.getvalue()


def to_png(img: Image.Image, max_width: int = 0) -> bytes:
    if max_width and img.width > max_width:
        img = img.resize((max_width, max(1, int(img.height * max_width / img.width))), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def to_jpeg(img: Image.Image, max_side: int = 2560) -> bytes:
    """Telegram'ga rasm sifatida yuborish uchun (tez yuklanadi)."""
    if max(img.size) > max_side:
        k = max_side / max(img.size)
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def merge_pdf(images: list[Image.Image], dpi: int = 150) -> bytes:
    """Bir nechta sahifani bitta PDF qilish (chop etish uchun)."""
    buf = BytesIO()
    if not images:
        return b""
    images[0].save(buf, "PDF", resolution=float(dpi), save_all=True, append_images=images[1:], quality=90)
    return buf.getvalue()


def new_id(design: dict, prefix: str = "e") -> str:
    ids = {e.get("id") for e in design.get("els", [])}
    i = len(ids) + 1
    while f"{prefix}{i}" in ids:
        i += 1
    return f"{prefix}{i}"


def deep(d: dict) -> dict:
    return copy.deepcopy(d)

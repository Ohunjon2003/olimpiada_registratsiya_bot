"""Avtomatik test tekshiruvi (OMR): titul yaratish, rasmdan o'qish, baholash, belgilangan nusxa.

Titul tuzilishi (barcha o'lchamlar mm da, bitta `Layout` dan — chop etish ham, o'qish ham shundan):
  * 4 burchakda ArUco marker: rasm qiyshiq/uzoqdan olinsa ham varaq aniq tekislanadi.
    Marker raqamlari titul turini ham bildiradi: Q (A4 ning 1/4) — 0..3, H (1/2) — 4..7, F (A4) — 8..11.
  * Nuqta-kod (QR o'rnida, 9×8 katak, har biri 2.2 mm): o'quvchi, fan, tur, variant, savollar soni va
    22 bitli imzo (HMAC, SECRET_KEY). QR'dan farqli — kataklar katta va tekislangan rasmdan doirachalar
    kabi o'qiladi, shuning uchun 720p kamera va uzoqdan ham ishonchli. Imzo mos kelmasa (bitta katak
    noto'g'ri o'qilsa ham) titul qabul qilinmaydi — tizim hech qachon boshqa o'quvchiga yozib yubormaydi.
  * ID doirachalari: o'quvchi o'z ID raqamini o'zi belgilaydi — kod bilan solishtiriladi
    (titul boshqa o'quvchiga berilib yuborilgan bo'lsa — aniqlanadi).
  * Javob doirachalari: A, B, C, D.

O'qish: markerlar → perspektiva tekislash → yorug'likni tekislash (fon bo'yicha) → har doirachaning
qorayganlik darajasi (0..1). Aniq bo'yalgan ≥ 0.40, bo'sh ≤ 0.22, oralig'i — «aniq emas»
(tizim taxmin qilmaydi, qayta skaner yoki admin tekshiruvi so'raladi).
"""
from __future__ import annotations

import hashlib
import hmac
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..config import config

OPTIONS = "ABCD"
MAX_QUESTIONS = 200
PX = 8.0               # tekislangan rasmda 1 mm = 8 piksel
FILLED, EMPTY = 0.40, 0.22
SOFT = 0.33            # bitta aniq belgi bo'lsa, qolganlari shu darajagacha bo'lishi mumkin (o'chirilgan iz)
R_MM = 1.75            # doiracha radiusi
COL_PITCH, ROW_PITCH = 5.0, 4.3
MARKER = 9.0           # marker tomoni
MARGIN = 4.0           # varaq chetidan marker
KINDS = {             # tur: (kenglik, balandlik, ustunlar, qatorlar, marker raqamlari boshi, A4 ga nechta)
    "Q": (105.0, 148.5, 2, 20, 0, 4),
    "H": (210.0, 148.5, 5, 20, 4, 2),
    "F": (210.0, 297.0, 5, 40, 8, 1),
}


def kind_for(n: int) -> str:
    if n <= 40:
        return "Q"
    if n <= 100:
        return "H"
    return "F"


@dataclass
class Layout:
    kind: str
    n: int          # savollar soni
    d: int          # ID raqamlari soni
    w: float
    h: float
    markers: dict[int, tuple[float, float]]           # marker raqami → markazi (mm)
    qr: tuple[float, float, float]                      # x, y, tomon
    id_bubbles: list[list[tuple[float, float]]]         # [raqam ustuni][0..9] → markaz
    q_bubbles: list[list[tuple[float, float]]]          # [savol][A..D] → markaz
    q_labels: list[tuple[float, float]]                 # savol raqami joyi
    col_heads: list[float]                              # har ustunning chap cheti (sarlavha uchun)
    y0: float

    @property
    def code_cells(self) -> list[tuple[float, float]]:
        """Nuqta-kod kataklarining markazlari (qatorma-qator, chapdan o'ngga), mm."""
        return code_cells(self.qr)


@lru_cache(maxsize=64)
def layout(n: int, d: int = 4) -> Layout:
    if not 1 <= n <= MAX_QUESTIONS:
        raise ValueError(f"Savollar soni 1..{MAX_QUESTIONS} bo'lishi kerak")
    d = max(4, min(6, d))
    kind = kind_for(n)
    w, h, cols, rows, base, _ = KINDS[kind]
    c = MARGIN + MARKER / 2
    markers = {base + 0: (c, c), base + 1: (w - c, c), base + 2: (w - c, h - c), base + 3: (c, h - c)}
    qr = (w - MARGIN - 20.0, 14.5, 20.0)
    y0 = 47.0
    id_x0 = 6.0 + COL_PITCH / 2
    id_bubbles = [[(id_x0 + j * COL_PITCH, y0 + 3.5 + v * ROW_PITCH) for v in range(10)] for j in range(d)]
    ax = 6.0 + d * COL_PITCH + 5.0
    col_w = 6.0 + len(OPTIONS) * COL_PITCH + 3.0
    q_bubbles, q_labels, heads = [], [], []
    for i in range(n):
        col, row = divmod(i, rows)
        x = ax + col * col_w
        y = y0 + row * ROW_PITCH
        q_labels.append((x + 5.0, y))
        q_bubbles.append([(x + 6.0 + COL_PITCH / 2 + k * COL_PITCH, y) for k in range(len(OPTIONS))])
        if row == 0:
            heads.append(x)
    return Layout(kind, n, d, w, h, markers, qr, id_bubbles, q_bubbles, q_labels, heads, y0)


# ---------------------------------------------------------------------------
# Nuqta-kod (imzolangan token)
# ---------------------------------------------------------------------------
CODE_COLS, CODE_CELL = 9, 2.2                   # 9 ustun, katak 2.2 mm


@dataclass(frozen=True)
class CodeSpec:
    """Titul kodi formati. v1 — 9×8 (sinfsiz, 2-noyabr versiyasi), v2 — 9×9 (sinf bilan)."""
    version: int
    rows: int
    fields: tuple

    @property
    def cells(self) -> int:
        return CODE_COLS * self.rows

    @property
    def sig_bits(self) -> int:
        return self.cells - sum(b for _, b in self.fields)


_BASE = (("ver", 2), ("pid", 18), ("sid", 10), ("rid", 8), ("var", 2), ("n", 8), ("d", 2))
SPEC1 = CodeSpec(1, 8, _BASE)                       # 50 bit + 22 bit imzo
SPEC2 = CodeSpec(2, 9, _BASE + (("grade", 8),))     # 58 bit + 23 bit imzo
SPECS = {1: SPEC1, 2: SPEC2}
CODE_SPEC = SPEC2                                   # yangi titullar shu formatda chop etiladi
CODE_ROWS = CODE_SPEC.rows
LIMITS = {name: (1 << bits) - 1 for name, bits in SPEC2.fields}


def code_cells(box: tuple[float, float, float], rows: int = CODE_ROWS) -> list[tuple[float, float]]:
    """Kod qutisi (x, y, tomon) ichidagi kataklar markazlari (qatorma-qator)."""
    bx, by, bs = box
    x0 = bx + (bs - CODE_COLS * CODE_CELL) / 2
    y0 = by + (bs - rows * CODE_CELL) / 2
    return [(x0 + (c + 0.5) * CODE_CELL, y0 + (r + 0.5) * CODE_CELL)
            for r in range(rows) for c in range(CODE_COLS)]


@dataclass
class Token:
    pid: int
    sid: int
    rid: int
    variant: int
    n: int
    d: int
    raw: str
    grade: Optional[int] = None     # titul qaysi sinf uchun chop etilgan (v1 titullarda yo'q)
    version: int = 2


def _vals(spec: CodeSpec, t: Token) -> dict[str, int]:
    return {"ver": spec.version, "pid": t.pid, "sid": t.sid, "rid": t.rid, "var": t.variant, "n": t.n,
            "d": t.d - 4, "grade": t.grade or 0}


def _pack(spec: CodeSpec, t: Token) -> int:
    vals, v = _vals(spec, t), 0
    for name, bits in spec.fields:
        x = vals[name]
        if not 0 <= x <= (1 << bits) - 1:
            raise ValueError(f"Titul kodi uchun {name}={x} juda katta (chegara {(1 << bits) - 1})")
        v = (v << bits) | x
    return v


def _unpack(spec: CodeSpec, v: int) -> dict[str, int]:
    out = {}
    for name, bits in reversed(spec.fields):
        out[name] = v & ((1 << bits) - 1)
        v >>= bits
    return out


def _sig_int(spec: CodeSpec, value: int) -> int:
    h = hmac.new(config.SECRET_KEY.encode(), f"OMR{spec.version}:{value}".encode(), hashlib.sha256).digest()
    return int.from_bytes(h[:4], "big") >> (32 - spec.sig_bits)


def _raw(t: Token) -> str:
    spec = SPECS[t.version]
    sig = _sig_int(spec, _pack(spec, t))
    if t.version == 1:
        return f"O2.{t.pid}.{t.sid}.{t.rid}.{t.variant}.{t.n}.{t.d}.{sig:x}"
    return f"O3.{t.pid}.{t.sid}.{t.rid}.{t.variant}.{t.n}.{t.d}.{t.grade or 0}.{sig:x}"


def make_token(pid: int, sid: int, rid: int, variant: int, n: int, d: int, grade: int = 0) -> str:
    """Token matni (chop etishda titul kodiga aylanadi):
    «O3.o'quvchi.fan.tur.variant.savollar.raqamlar.sinf.imzo» (sinf — Grade.id)."""
    return _raw(Token(pid, sid, rid, variant, n, d, "", grade, 2))


def token_bits(raw: str) -> list[int]:
    """Token → bitlar (katta bitdan). Noto'g'ri matn (masalan, qo'llanmadagi namuna) — imzosiz «shovqin»."""
    tok = parse_token(raw)
    if tok is None:
        h = int.from_bytes(hashlib.sha256(("x" + (raw or "")).encode()).digest(), "big")
        return [(h >> i) & 1 for i in range(CODE_SPEC.cells)]
    spec = SPECS[tok.version]
    value = _pack(spec, tok)
    v = (value << spec.sig_bits) | _sig_int(spec, value)
    return [(v >> (spec.cells - 1 - i)) & 1 for i in range(spec.cells)]


def token_from_bits(bits: list[int], spec: CodeSpec = CODE_SPEC) -> Optional[Token]:
    """Bitlar → Token (imzo tekshiriladi; mos kelmasa — None)."""
    if len(bits) != spec.cells:
        return None
    v = 0
    for b in bits:
        v = (v << 1) | (1 if b else 0)
    value, sig = v >> spec.sig_bits, v & ((1 << spec.sig_bits) - 1)
    if not hmac.compare_digest(_sig_int(spec, value).to_bytes(4, "big"), sig.to_bytes(4, "big")):
        return None
    f = _unpack(spec, value)
    if f["ver"] != spec.version:
        return None
    tok = Token(f["pid"], f["sid"], f["rid"], f["var"], f["n"], f["d"] + 4, "",
                f.get("grade") if spec.version >= 2 else None, spec.version)
    if not (1 <= tok.n <= MAX_QUESTIONS and 4 <= tok.d <= 6):
        return None
    tok.raw = _raw(tok)
    return tok


def parse_token(raw: str) -> Optional[Token]:
    """Token matnini tekshiradi (imzo bilan): noto'g'ri bo'lsa None. «O2.» — v1, «O3.» — v2 (sinf bilan)."""
    parts = (raw or "").strip().split(".")
    try:
        if len(parts) == 8 and parts[0] == "O2":
            pid, sid, rid, var, n, d = (int(x) for x in parts[1:7])
            tok = Token(pid, sid, rid, var, n, d, "", None, 1)
        elif len(parts) == 9 and parts[0] == "O3":
            pid, sid, rid, var, n, d, grade = (int(x) for x in parts[1:8])
            tok = Token(pid, sid, rid, var, n, d, "", grade, 2)
        else:
            return None
        sig = int(parts[-1], 16)
        spec = SPECS[tok.version]
        value = _pack(spec, tok)
    except ValueError:
        return None
    if _sig_int(spec, value) != sig or not (1 <= tok.n <= MAX_QUESTIONS and 4 <= tok.d <= 6):
        return None
    tok.raw = raw.strip()
    return tok


def variant_letter(v: int) -> str:
    return "ABCD"[v] if 0 <= v < 4 else "?"


def variant_for_seat(row: int, desk: int, seat: int, seats_per_desk: int, variants: int) -> int:
    """Shaxmat tartibi: yonma-yon va old-orqadagilar turli variantda."""
    if variants <= 1:
        return 0
    x = (desk - 1) * seats_per_desk + (seat - 1)
    return (row + x) % variants


# ---------------------------------------------------------------------------
# Titulni chizish (chop etish uchun, Pillow)
# ---------------------------------------------------------------------------
def _font(size_px: float, bold: bool = False) -> ImageFont.FreeTypeFont:
    from . import design as dz
    return dz.pil_font(dz.STATIC_FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), max(6.0, size_px))


@lru_cache(maxsize=16)
def _marker_img(mid: int, side_px: int) -> Image.Image:
    import cv2
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    m = cv2.aruco.generateImageMarker(d, mid, 6 * 20)
    return Image.fromarray(m).resize((side_px, side_px), Image.NEAREST)


@dataclass
class CardInfo:
    token: str
    olympiad: str
    subject: str
    code: str
    place: str = ""       # «101-xona · 14-o'rin»
    variant: str = ""     # «B variant»
    name: str = ""        # ixtiyoriy
    session: str = ""
    grade: str = ""       # «7-sinf» — titulda katta, qora fonda (titullar sinflar orasida aralashmasin)


def render_card(lay: Layout, info: CardInfo, dpi: int = 200) -> Image.Image:
    """Bitta titul (oq-qora, L rejim)."""
    k = dpi / 25.4
    W, H = round(lay.w * k), round(lay.h * k)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)

    def P(x: float, y: float) -> tuple[float, float]:
        return x * k, y * k

    def text(x, y, s, size_mm, bold=False, fill=0, anchor="la", max_w=None):
        f = _font(size_mm * k, bold)
        s = str(s)
        if max_w:
            while s and d.textlength(s, font=f) > max_w * k:
                s = s[:-1]
        d.text(P(x, y), s, font=f, fill=fill, anchor=anchor)

    # Markerlar
    side = round(MARKER * k)
    for mid, (cx, cy) in lay.markers.items():
        img.paste(_marker_img(mid, side), (round((cx - MARKER / 2) * k), round((cy - MARKER / 2) * k)))
    # Nuqta-kod (QR o'rnida): 1 — qora katak, 0 — oq. Atrofida ingichka ramka
    qx, qy, qs = lay.qr
    gap = 0.18
    bits = token_bits(info.token)
    rows = len(bits) // CODE_COLS
    for bit, (cx, cy) in zip(bits, code_cells(lay.qr, rows)):
        if bit:
            d.rectangle([P(cx - CODE_CELL / 2 + gap, cy - CODE_CELL / 2 + gap),
                         P(cx + CODE_CELL / 2 - gap, cy + CODE_CELL / 2 - gap)], fill=0)
    fx0 = qx + (qs - CODE_COLS * CODE_CELL) / 2 - 0.9
    fy0 = qy + (qs - rows * CODE_CELL) / 2 - 0.8
    d.rectangle([P(fx0, fy0), P(qx + qs - (fx0 - qx), qy + qs - (fy0 - qy))], outline=150, width=max(1, round(0.2 * k)))
    text(qx + qs / 2, qy + qs - (fy0 - qy) + 0.35, "titul kodi — tegmang", 1.4, fill=130, anchor="ma")
    # Sarlavha
    top_w = lay.w - 2 * (MARGIN + MARKER) - 4
    text(lay.w / 2, MARGIN + 1.0, info.olympiad, 2.6, anchor="ma", fill=60, max_w=top_w)
    text(lay.w / 2, MARGIN + 5.0, "JAVOBLAR VARAQASI (TITUL)", 2.3, True, anchor="ma", fill=90, max_w=top_w)
    tx, tw = 6.0, qx - 8.0
    text(tx, 15.0, info.subject + (f"  ·  {info.variant}" if info.variant else ""), 3.6, True, max_w=tw)
    text(tx, 20.5, f"ID {info.code}", 5.2, True)
    if info.grade:
        idw = d.textlength(f"ID {info.code}", font=_font(5.2 * k, True)) / k
        gf = _font(4.2 * k, True)
        gw = d.textlength(info.grade.upper(), font=gf) / k
        gx = tx + idw + 3.5
        if gx + gw + 3.0 <= qx - 2:
            d.rounded_rectangle([P(gx, 20.0), P(gx + gw + 3.0, 26.6)], radius=1.2 * k, fill=0)
            d.text(P(gx + 1.5 + gw / 2, 23.3), info.grade.upper(), font=gf, fill=255, anchor="mm")
    text(tx, 28.0, info.place or info.session, 3.1, max_w=tw)
    if info.name:
        text(tx, 32.5, info.name, 2.7, fill=40, max_w=tw)
    else:
        text(tx, 32.5, "Ism yozilmaydi — faqat ID", 2.4, fill=110, max_w=tw)
    d.line([P(4, 37.2), P(lay.w - 4, 37.2)], fill=120, width=max(1, round(0.25 * k)))

    lw = max(1, round(0.22 * k))
    small = _font(2.0 * k)
    # ID: yozish kataklari + doirachalar
    text(6.0, 38.3, "ID raqamingiz:", 2.4, True)
    for j, col in enumerate(lay.id_bubbles):
        bx = col[0][0]
        d.rectangle([P(bx - 2.2, 41.6), P(bx + 2.2, 46.4)], outline=60, width=lw)
        for v, (x, y) in enumerate(col):
            d.ellipse([P(x - R_MM, y - R_MM), P(x + R_MM, y + R_MM)], outline=40, width=lw)
            d.text(P(x, y), str(v), font=small, fill=165, anchor="mm")
    # Javoblar
    head_font = _font(2.2 * k, True)
    for hx in lay.col_heads:
        for kk, letter in enumerate(OPTIONS):
            d.text(P(hx + 6.0 + COL_PITCH / 2 + kk * COL_PITCH, lay.y0 - 4.2), letter, font=head_font, fill=80, anchor="mm")
    qfont = _font(2.3 * k, True)
    for i, (row, (lx, ly)) in enumerate(zip(lay.q_bubbles, lay.q_labels)):
        d.text(P(lx, ly), str(i + 1), font=qfont, fill=20, anchor="rm")
        for kk, (x, y) in enumerate(row):
            d.ellipse([P(x - R_MM, y - R_MM), P(x + R_MM, y + R_MM)], outline=40, width=lw)
            d.text(P(x, y), OPTIONS[kk], font=small, fill=165, anchor="mm")
    # Pastki izoh
    bottom = lay.h - MARGIN - MARKER / 2
    text(lay.w / 2, bottom, "Doirachani to'liq bo'yang ●  Galochka va «x» o'qilmaydi  Bukilmang", 2.1, anchor="mm",
         fill=90, max_w=lay.w - 2 * (MARGIN + MARKER) - 2)
    return img


def compose_pages(cards: list[Image.Image], kind: str, dpi: int = 200) -> list[Image.Image]:
    """Titullarni A4 varaqlarga joylash (Q — 4 ta, H — 2 ta, F — 1 ta) + qirqish chiziqlari."""
    k = dpi / 25.4
    PW, PH = round(210 * k), round(297 * k)
    per = KINDS[kind][5]
    pages = []
    for i in range(0, len(cards), per):
        page = Image.new("L", (PW, PH), 255)
        d = ImageDraw.Draw(page)
        for j, c in enumerate(cards[i:i + per]):
            if kind == "Q":
                x, y = (j % 2) * PW // 2, (j // 2) * PH // 2
            elif kind == "H":
                x, y = 0, j * PH // 2
            else:
                x, y = 0, 0
            page.paste(c, (x, y))
        # Qirqish chiziqlari (punktir)
        def dash(x1, y1, x2, y2):
            L = math.hypot(x2 - x1, y2 - y1)
            n = int(L // (3 * k))
            for t in range(0, n, 2):
                a, b = t / n, min(1, (t + 1) / n)
                d.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a), (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], fill=170, width=1)
        if kind in ("Q", "H"):
            dash(0, PH / 2, PW, PH / 2)
        if kind == "Q":
            dash(PW / 2, 0, PW / 2, PH)
        pages.append(page)
    return pages


# ---------------------------------------------------------------------------
# Rasmdan o'qish (OpenCV)
# ---------------------------------------------------------------------------
@dataclass
class ReadResult:
    state: str                    # nosheet | partial | error | ok
    message: str = ""
    token: Optional[Token] = None
    answers: str = ""             # A..D, «-» bo'sh, «*» bir nechta, «?» aniq emas
    id_marked: str = ""           # raqamlar; «_» bo'sh, «*» bir nechta, «?» aniq emas
    unclear: list[int] = field(default_factory=list)   # aniq o'qilmagan savollar (1 dan)
    warped: object = None         # tekislangan oq-qora rasm (numpy)
    fills: list = field(default_factory=list)


_detector = None


def _aruco():
    global _detector
    if _detector is None:
        import cv2
        p = cv2.aruco.DetectorParameters()
        p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        p.adaptiveThreshWinSizeMax = 53
        _detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), p)
    return _detector


def _read_code(norm, span: float, cells: list[tuple[float, float]], spec: "CodeSpec" = None):
    """Nuqta-kodni o'qiydi. Qaytaradi: (Token yoki None, crisp).

    crisp=True — kataklar aniq qora/oq edi (imzo mos kelmasa — bu boshqa olimpiada tituli).
    Kichik siljishlarga chidamli: butun blok ±0.6 mm oralig'ida eng kontrastli holatga suriladi.
    """
    import numpy as np
    h = max(1, int(CODE_CELL * PX * 0.25))
    best = None
    for dy in (0.0, -0.4, 0.4, -0.8, 0.8):
        for dx in (0.0, -0.4, 0.4, -0.8, 0.8):
            vals = []
            for (x, y) in cells:
                cx, cy = int(round((x + dx) * PX)), int(round((y + dy) * PX))
                patch = norm[cy - h:cy + h + 1, cx - h:cx + h + 1]
                vals.append((255.0 - float(patch.mean())) / span if patch.size else 0.0)
            v = np.clip(np.array(vals), 0, 1)
            score = float(np.abs(v - 0.5).mean())
            if best is None or score > best[0]:
                best = (score, v)
            if score > 0.38:          # aniq — qolgan siljishlarni sinab o'tirmaymiz
                break
        if best[0] > 0.38:
            break
    v = best[1]
    # 1) qat'iy chegara 0.5; 2) kataklar ikki guruhga ajralgan bo'lsa — ular o'rtasidagi chegara
    thresholds = [0.5]
    sv = np.sort(v)
    gaps = np.diff(sv)
    if len(gaps):
        i = int(np.argmax(gaps))
        if gaps[i] > 0.15:
            thresholds.append(float((sv[i] + sv[i + 1]) / 2))
    for t in thresholds:
        tok = token_from_bits([1 if x >= t else 0 for x in v], spec or CODE_SPEC)
        if tok is not None:
            return tok, True
    crisp = bool(np.min(np.abs(v - 0.5)) > 0.22)
    return None, crisp


def _find_markers(gray, small, det_scale: float):
    """4 burchak markerini bir necha usulda qidiradi (oddiy → kontrast → kattalashtirish → to'liq o'lcham).

    Qaytaradi: ({tur: {marker_id: (x, y)}}, bir xil marker 2 marta topildimi, umuman marker bormi).
    Birinchi to'liq (4/4) natija olinishi bilan to'xtaydi — oddiy holatda faqat 1 urinish (tez).
    """
    import cv2
    tries = [(small, det_scale)]
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    tries.append((clahe.apply(small), det_scale))
    if max(small.shape) < 1400:                 # 720p va kichik kadr: kichik markerlar uchun kattalashtiramiz
        up = cv2.resize(small, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
        tries.append((up, det_scale * 1.6))
    if det_scale < 1:                           # katta kadr: to'liq o'lchamda ham
        tries.append((gray, 1.0))
    sharp = cv2.addWeighted(small, 1.8, cv2.GaussianBlur(small, (0, 0), 2.0), -0.8, 0)
    tries.append((sharp, det_scale))
    best: dict[str, dict[int, tuple[float, float]]] = {}
    any_marker = False
    for n_try, (img, sc) in enumerate(tries):
        if n_try >= 2 and not any_marker:
            break                                   # 2 usulda ham bitta marker yo'q — kadrda titul yo'q
        corners, ids, _ = _aruco().detectMarkers(img)
        if ids is None or len(ids) == 0:
            continue
        any_marker = True
        found: dict[str, dict[int, tuple[float, float]]] = {}
        dup = False
        for cs, mid in zip(corners, (int(i) for i in ids.flatten())):
            for kind, (_, _, _, _, base, _) in KINDS.items():
                if base <= mid < base + 4:
                    c = cs.reshape(4, 2).mean(axis=0) / sc
                    if mid in found.setdefault(kind, {}):
                        dup = True
                    found[kind][mid] = (float(c[0]), float(c[1]))
        if dup:
            return found, True, True
        if any(len(v) == 4 for v in found.values()):
            return found, False, True
        # to'liq emas — eng ko'p topilganini eslab qolamiz va keyingi usulni sinaymiz
        for kind, v in found.items():
            if len(v) > len(best.get(kind, {})):
                best[kind] = v
    # 3 ta marker topilgan bo'lsa — 4-sining joyini hisoblab, o'sha joyni kattalashtirib qidiramiz
    for kind, v in best.items():
        if len(v) == 3:
            got = _find_fourth(gray, kind, v)
            if got:
                v.update(got)
                return best, False, True
    return best, False, any_marker


def _find_fourth(gray, kind: str, have: dict[int, tuple[float, float]]) -> dict[int, tuple[float, float]]:
    """3 ta ma'lum marker bo'yicha 4-sini taxminiy joyda (kattalashtirib, kontrastni oshirib) qidiradi."""
    import cv2
    import numpy as np
    base = KINDS[kind][4]
    mm = layout(1 if kind == "Q" else (41 if kind == "H" else 101), 4).markers
    missing = [m for m in range(base, base + 4) if m not in have]
    if len(missing) != 1:
        return {}
    known = sorted(have)
    A = cv2.getAffineTransform(np.float32([mm[k] for k in known]), np.float32([have[k] for k in known]))
    mx, my = mm[missing[0]]
    px, py = A @ np.array([mx, my, 1.0])
    scale = float(np.hypot(A[0, 0], A[1, 0]))          # piksel / mm
    half = int(MARKER * scale * 1.6) + 8
    x0, y0 = max(0, int(px) - half), max(0, int(py) - half)
    crop = gray[y0:int(py) + half, x0:int(px) + half]
    if crop.size == 0:
        return {}
    k = max(1.0, min(4.0, 70.0 / max(1.0, MARKER * scale)))   # marker ~70 px bo'lsin
    big = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(4, 4))
    for img in (big, clahe.apply(big)):
        corners, ids, _ = _aruco().detectMarkers(img)
        if ids is None:
            continue
        for cs, mid in zip(corners, (int(i) for i in ids.flatten())):
            if mid == missing[0]:
                c = cs.reshape(4, 2).mean(axis=0) / k
                cx, cy = float(c[0] + x0), float(c[1] + y0)
                # taxmin qilingan joydan juda uzoq bo'lsa — qabul qilmaymiz
                if np.hypot(cx - px, cy - py) <= MARKER * scale * 1.2:
                    return {mid: (cx, cy)}
    return {}


def read_image(data: bytes) -> ReadResult:
    """Titul suratini o'qiydi (bazaga tegmaydi)."""
    import cv2
    import numpy as np
    arr = np.frombuffer(data, np.uint8)
    gray = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        return ReadResult("error", "Rasmni o'qib bo'lmadi")
    # Juda katta rasmni kichraytiramiz (tezlik uchun), lekin tafsilot yo'qolmasin
    long = max(gray.shape)
    if long > 3200:
        s = 3200 / long
        gray = cv2.resize(gray, (int(gray.shape[1] * s), int(gray.shape[0] * s)), interpolation=cv2.INTER_AREA)
    det_scale = min(1.0, 1600 / max(gray.shape))
    small = cv2.resize(gray, None, fx=det_scale, fy=det_scale, interpolation=cv2.INTER_AREA) if det_scale < 1 else gray
    found, dup, any_marker = _find_markers(gray, small, det_scale)
    if not any_marker:
        return ReadResult("nosheet", "Titul ko'rinmayapti")
    if dup:
        return ReadResult("partial", "Kadrda bir nechta titul bor — bittasini skaner qiling")
    full = [k for k, v in found.items() if len(v) == 4]
    if not full:
        best = max(found.values(), key=len) if found else {}
        return ReadResult("partial", f"Titulning {len(best)}/4 burchagi ko'rinyapti — butun titulni ramkaga joylang")
    kind = full[0]
    w, h, _, _, base, _ = KINDS[kind]
    # Savollar soni va ID uzunligi koddan bilinadi; avval tekislaymiz (Q/H/F o'lchami markerlardan ma'lum)
    tmp = layout(1 if kind == "Q" else (41 if kind == "H" else 101), 4)
    src = np.float32([found[kind][base + i] for i in range(4)])
    dst = np.float32([[x * PX, y * PX] for x, y in (tmp.markers[base + i] for i in range(4))])
    M = cv2.getPerspectiveTransform(src, dst)
    W, H = int(round(w * PX)), int(round(h * PX))
    warped = cv2.warpPerspective(gray, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=255)
    # Titul kadrda juda kichik bo'lsa (1 mm ga 3 pikseldan kam) — doirachalarni ishonchli o'qib bo'lmaydi
    top_px = float(np.hypot(*(src[1] - src[0])))
    top_mm = tmp.markers[base + 1][0] - tmp.markers[base][0]
    if top_px / top_mm < 3.0:
        hint = " (katta titul — telefonni yotqizib, gorizontal tuting)" if kind != "Q" else ""
        return ReadResult("partial", "Titul juda uzoqda — yaqinroq olib keling" + hint)
    # Yorug'likni tekislash: fon (qora belgilarsiz) bo'yicha
    bg = cv2.morphologyEx(warped, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)))
    bg = cv2.GaussianBlur(bg, (0, 0), 15)
    norm = np.clip(warped.astype(np.float32) / np.maximum(bg.astype(np.float32), 1) * 255.0, 0, 255)
    # Siyoh (qora) darajasi: marker ichidagi eng qora piksellar
    mk = []
    for (cx, cy) in tmp.markers.values():
        r0 = int(MARKER / 2 * PX * 0.9)
        patch = norm[int(cy * PX) - r0:int(cy * PX) + r0, int(cx * PX) - r0:int(cx * PX) + r0]
        if patch.size:
            mk.append(float(np.percentile(patch, 10)))
    ink = min(120.0, max(0.0, float(np.median(mk)) if mk else 40.0))
    span = max(255.0 - ink, 60.0)
    # Nuqta-kod: kim, qaysi fan, qaysi tur (imzo bilan)
    # Avval yangi format (sinf bilan, 9×9), bo'lmasa — eski (9×8) titul
    tok, crisp = _read_code(norm, span, code_cells(tmp.qr, SPEC2.rows), SPEC2)
    if tok is None:
        tok1, crisp1 = _read_code(norm, span, code_cells(tmp.qr, SPEC1.rows), SPEC1)
        tok, crisp = (tok1, True) if tok1 else (None, crisp and crisp1)
    if tok is None:
        if crisp:
            return ReadResult("error", "Bu titul shu olimpiadaga tegishli emas (kod imzosi mos emas)")
        return ReadResult("partial", "Titul kodi o'qilmadi — yorug'roq joyda, to'g'riroq tuting")
    if kind_for(tok.n) != kind:
        return ReadResult("error", "Titul turi kod bilan mos emas")
    lay = layout(tok.n, tok.d)
    # Xiralik
    # Kichik kadrdan (720p, uzoqdan) tekislangan rasm tabiiy ravishda «yumshoq» — chegarani masshtabga moslaymiz
    px_mm = top_px / top_mm
    blur_min = max(6.0, min(25.0, 25.0 * (px_mm / PX) ** 2))
    blur = cv2.Laplacian(warped[int(lay.y0 * PX):, :], cv2.CV_64F).var()
    if blur < blur_min:
        return ReadResult("partial", "Rasm xira — telefonni qimirlatmang, fokusni kuting", token=tok)
    rin = R_MM * PX * 0.62
    ring_in, ring_out = R_MM * PX * 0.82, R_MM * PX * 1.15
    yy, xx = np.mgrid[-int(ring_out) - 1:int(ring_out) + 2, -int(ring_out) - 1:int(ring_out) + 2]
    dist = np.sqrt(xx * xx + yy * yy)
    m_in = dist <= rin
    m_ring = (dist >= ring_in) & (dist <= ring_out)
    off = int(ring_out) + 1

    def sample(x_mm: float, y_mm: float) -> tuple[float, float]:
        cx, cy = int(round(x_mm * PX)), int(round(y_mm * PX))
        patch = norm[cy - off:cy + off + 1, cx - off:cx + off + 1]
        if patch.shape != m_in.shape:
            return 0.0, 0.0
        f_in = (255.0 - float(patch[m_in].mean())) / span
        f_ring = (255.0 - float(patch[m_ring].mean())) / span
        return max(0.0, min(1.0, f_in)), max(0.0, min(1.0, f_ring))

    # Joylashuv to'g'rimi: chop etilgan doiracha chegaralari kutilgan joyda bo'lishi kerak
    rings, fills = [], []
    for row in lay.q_bubbles:
        rf = []
        for (x, y) in row:
            fi, fr = sample(x, y)
            rf.append(fi)
            rings.append(fr)
        fills.append(rf)
    if float(np.median(rings)) < 0.06:
        return ReadResult("partial", "Titul to'liq tekislanmadi — qayta urinib ko'ring", token=tok)

    def decide(vals: list[float]) -> str:
        """Bitta savol yoki ID ustuni uchun qaror: indeks, «-» bo'sh, «*» bir nechta, «?» aniq emas."""
        filled = [i for i, v in enumerate(vals) if v >= FILLED]
        if len(filled) >= 2:
            return "*"
        if len(filled) == 1:
            others = [v for i, v in enumerate(vals) if i != filled[0]]
            return str(filled[0]) if all(v <= SOFT for v in others) else "?"
        return "-" if all(v <= EMPTY for v in vals) else "?"

    answers, unclear = [], []
    for i, vals in enumerate(fills):
        r = decide(vals)
        if r.isdigit():
            answers.append(OPTIONS[int(r)])
        else:
            answers.append(r)
            if r == "?":
                unclear.append(i + 1)
    idm = []
    for col in lay.id_bubbles:
        vals = [sample(x, y)[0] for (x, y) in col]
        r = decide(vals)
        idm.append(r if r.isdigit() else {"-": "_"}.get(r, r))
    return ReadResult("ok", "", tok, "".join(answers), "".join(idm), unclear, warped, fills)


# ---------------------------------------------------------------------------
# Baholash va belgilangan nusxa
# ---------------------------------------------------------------------------
def clean_key(raw: str, n: int) -> tuple[str, Optional[str]]:
    """«ABCD ...» → «ABCD...». (kalit, xato matni)"""
    s = "".join(ch for ch in (raw or "").upper().replace("А", "A").replace("В", "B").replace("С", "C")
                if not ch.isspace() and ch not in ",.;-|0123456789)")
    bad = sorted({ch for ch in s if ch not in OPTIONS})
    if bad:
        return s, f"Faqat {', '.join(OPTIONS)} harflari bo'lishi mumkin (noto'g'ri: {''.join(bad)})"
    if len(s) != n:
        return s, f"Kalitda {len(s)} ta javob, fanda esa {n} ta savol"
    return s, None


def grade(answers: str, key: str) -> list[bool]:
    """Har savol to'g'rimi («*» va «-» — xato/javobsiz)."""
    return [a == k for a, k in zip(answers, key)]


def score_values(subject, ok: list[bool]) -> dict[str, str]:
    """scoring.compute uchun qiymatlar: uniform — {correct}, groups — har guruhdagi to'g'rilar."""
    if subject.scoring_mode == "groups":
        vals, i = {}, 0
        for g in subject.groups:
            vals[f"g{g.id}"] = str(sum(ok[i:i + g.question_count]))
            i += g.question_count
        return vals
    return {"correct": str(sum(ok))}


def annotate(warped, n: int, d: int, answers: str, key: str = "", id_marked: str = "",
             title: str = "") -> Image.Image:
    """Tekshirilgan nusxa: tanlangan to'g'ri — yashil, xato — qizil, to'g'ri javob (xato bo'lsa) — yashil halqa,
    bir nechta belgi — to'q sariq, aniq emas — binafsha."""
    import numpy as np
    lay = layout(n, d)
    base = Image.fromarray(warped if isinstance(warped, np.ndarray) else np.array(warped)).convert("RGB")
    over = Image.new("RGBA", base.size, (0, 0, 0, 0))
    dr = ImageDraw.Draw(over)
    r = R_MM * PX * 1.25
    GREEN, RED, ORANGE, PURPLE = (22, 163, 74), (220, 38, 38), (234, 88, 12), (147, 51, 234)
    for i, row in enumerate(lay.q_bubbles):
        a = answers[i] if i < len(answers) else "-"
        k = key[i] if key and i < len(key) else ""
        for j, (x, y) in enumerate(row):
            cx, cy = x * PX, y * PX
            box = [cx - r, cy - r, cx + r, cy + r]
            letter = OPTIONS[j]
            if a == letter:
                col = GREEN if (k and letter == k) else (RED if k else (37, 99, 235))
                dr.ellipse(box, outline=col + (255,), width=4, fill=col + (60,))
            elif k and letter == k and a != k:
                dr.ellipse(box, outline=GREEN + (255,), width=3)
        lx, ly = lay.q_labels[i]
        mx, my, u = lx * PX - PX * 5.6, ly * PX, PX * 1.1   # belgi joyi (savol raqamidan chapda)
        if a == "*":
            dr.text((mx + u, my), "2+", font=_font(PX * 2.4, True), fill=ORANGE + (255,), anchor="rm")
        elif a == "?":
            dr.text((mx + u, my), "?", font=_font(PX * 2.6, True), fill=PURPLE + (255,), anchor="rm")
        elif k and a == k:   # ✓ — chiziqlar bilan (shriftga bog'liq emas)
            dr.line([(mx - u, my), (mx - u * 0.2, my + u * 0.8), (mx + u, my - u)], fill=GREEN + (255,), width=4)
        elif k:              # ✗
            dr.line([(mx - u, my - u), (mx + u, my + u)], fill=RED + (255,), width=4)
            dr.line([(mx - u, my + u), (mx + u, my - u)], fill=RED + (255,), width=4)
    img = Image.alpha_composite(base.convert("RGBA"), over).convert("RGB")
    if title:
        bar = Image.new("RGB", (img.width, int(PX * 9)), (15, 23, 42))
        ImageDraw.Draw(bar).text((PX * 3, bar.height / 2), title, font=_font(PX * 4.2, True), fill=(255, 255, 255), anchor="lm")
        out = Image.new("RGB", (img.width, img.height + bar.height), "white")
        out.paste(bar, (0, 0))
        out.paste(img, (0, bar.height))
        return out
    return img

"""O'quvchilar uchun «Titulni to'g'ri to'ldirish» qo'llanmasi (A4, rangli).

Sahifa tarkibi:
  * haqiqiy titul namunasi (ID va javoblar bo'yalgan, «NAMUNA» belgisi bilan) va raqamli izohlar;
  * to'g'ri / noto'g'ri bo'yash misollari (ikkita belgi — xato hisoblanadi);
  * muhim qoidalar.
Admin uni PNG yoki PDF qilib yuklab oladi (chop etish yoki botda yuborish uchun), o'quvchilar esa
ochiq `/qollanma` sahifasida telefonda ko'radi.
"""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw

from . import omr

BLUE = (36, 83, 214)
GREEN = (18, 128, 92)
RED = (192, 38, 45)
ORANGE = (230, 120, 20)
INK = (27, 35, 51)
MUTED = (102, 112, 133)
SOFT = (244, 246, 250)

SAMPLE_ID = "0127"
SAMPLE_ANSWERS = "ABCDBADCABCDADBCABDCBACDABDCBA"   # 30 ta


def _wrap(d: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
    """So'zlarni qatorga bo'ladi (kenglik piksel hisobida)."""
    lines, cur = [], ""
    for word in text.split():
        t = (cur + " " + word).strip()
        if d.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _sample_card(olympiad: str, dpi: int):
    """Bo'yalgan namunaviy titul (RGB) va uning joylashuvi (Layout)."""
    lay = omr.layout(len(SAMPLE_ANSWERS), 4)
    info = omr.CardInfo(token="NAMUNA", olympiad=olympiad, subject="Matematika", code=SAMPLE_ID,
                        place="101-xona · 14-o'rin", variant="A variant")
    img = omr.render_card(lay, info, dpi).convert("RGB")
    k = dpi / 25.4
    d = ImageDraw.Draw(img)
    r = omr.R_MM * k * 0.92

    def fill(x, y):
        d.ellipse([x * k - r, y * k - r, x * k + r, y * k + r], fill=(25, 30, 60))
    for j, ch in enumerate(SAMPLE_ID):                      # ID doirachalari
        fill(*lay.id_bubbles[j][int(ch)])
    for i, a in enumerate(SAMPLE_ANSWERS):                  # javoblar
        fill(*lay.q_bubbles[i]["ABCD".index(a)])
    return img, lay


@lru_cache(maxsize=8)
def render_guide(olympiad: str = "Olimpiada", dpi: int = 200) -> Image.Image:
    """To'liq qo'llanma sahifasi (A4, RGB)."""
    k = dpi / 25.4
    W, H = round(210 * k), round(297 * k)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def F(mm, bold=False):
        return omr._font(mm * k, bold)

    def T(x, y, s, mm, bold=False, fill=INK, anchor="la"):
        d.text((x * k, y * k), s, font=F(mm, bold), fill=fill, anchor=anchor)

    def para(x, y, s, mm, w_mm, fill=INK, bold=False, gap=1.25):
        f = F(mm, bold)
        for line in _wrap(d, s, f, w_mm * k):
            d.text((x * k, y * k), line, font=f, fill=fill)
            y += mm * gap + 0.4
        return y

    def badge(cx, cy, n, color, rr=4.2):
        d.ellipse([(cx - rr) * k, (cy - rr) * k, (cx + rr) * k, (cy + rr) * k], fill=color, outline="white", width=max(2, round(0.5 * k)))
        d.text((cx * k, cy * k), str(n), font=F(rr * 1.25, True), fill="white", anchor="mm")

    # --- Sarlavha ---
    d.rectangle([0, 0, W, 27 * k], fill=BLUE)
    T(10, 6.5, "TITULNI TO'G'RI TO'LDIRISH QO'LLANMASI", 7.4, True, "white")
    T(10, 17.5, "To'g'ri to'ldirilgan titul — natijangiz tez va xatosiz hisoblanadi", 3.9, False, (214, 226, 255))

    # --- Namunaviy titul ---
    card, lay = _sample_card(olympiad, dpi)
    cx0, cy0 = 8.0, 33.0
    d.rectangle([(cx0 - 1) * k, (cy0 - 1) * k, (cx0 + lay.w + 1) * k, (cy0 + lay.h + 1) * k], outline=(200, 206, 218), width=max(2, round(0.4 * k)))
    img.paste(card, (round(cx0 * k), round(cy0 * k)))
    # «NAMUNA» tamg'asi (haqiqiy titul bilan adashtirmaslik uchun) — burchak o'rtasida
    stamp = Image.new("RGBA", (round(90 * k), round(20 * k)), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.text((stamp.width / 2, stamp.height / 2), "NAMUNA", font=F(15, True), fill=(192, 38, 45, 70), anchor="mm")
    stamp = stamp.rotate(28, expand=True, resample=Image.BICUBIC)
    img.paste(stamp, (round((cx0 + lay.w / 2) * k - stamp.width / 2), round((cy0 + lay.h * 0.60) * k - stamp.height / 2)), stamp)

    # izoh belgilari (raqamli doiralar) titul ustida
    qx, qy, qs = lay.qr
    badge(cx0 + 16.0, cy0 + 94.0, 1, ORANGE)                        # ID
    badge(cx0 + lay.col_heads[0] + 16.0, cy0 + lay.y0 - 11.0, 2, GREEN)   # javoblar
    badge(cx0 + qx - 4.0, cy0 + qy + qs / 2, 3, RED)                # QR
    c = omr.MARGIN + omr.MARKER / 2
    badge(cx0 + c + 8.0, cy0 + lay.h - c - 9.0, 4, BLUE)             # markerlar

    # --- O'ng ustun: qadamlar ---
    rx, rw = 118.0, 84.0
    steps = [
        (ORANGE, "ID raqamingizni belgilang",
         f"ID raqamingiz ruxsatnomada va botda yozilgan (masalan, {SAMPLE_ID}). Uni kataklarga yozing va har ustunda "
         f"FAQAT BITTA doirachani bo'yang: {SAMPLE_ID[0]}, {SAMPLE_ID[1]}, {SAMPLE_ID[2]}, {SAMPLE_ID[3]}."),
        (GREEN, "Javoblarni belgilang",
         "Har savolga A, B, C yoki D dan faqat BITTA doirachani to'liq bo'yang. Ikkita belgilangan savol xato hisoblanadi. "
         "Bilmagan savolingizni bo'sh qoldiring."),
        (RED, "QR kodga tegmang",
         "QR kod sizning ishingizni aniqlaydi. Uni yopmang, chizmang, ustiga yozmang va titulni bukmang."),
        (BLUE, "Burchaklardagi qora kvadratlar",
         "Ular telefon kamerasi titulni to'g'ri o'qishi uchun kerak. Ularni ifloslamang va qirqmang."),
    ]
    y = 35.0
    for n, (col, title, body) in enumerate(steps, 1):
        badge(rx + 4.2, y + 4.6, n, col)
        T(rx + 11.0, y + 1.2, title, 4.4, True, col)
        y = para(rx + 11.0, y + 7.6, body, 3.45, rw - 11.0, INK)
        y += 4.8

    # Titulni tekshirish ogohlantirishi (chiroyli quti)
    by0 = y + 0.5
    d.rounded_rectangle([rx * k, by0 * k, (rx + rw) * k, (by0 + 27.5) * k], radius=2.5 * k, fill=(255, 243, 224), outline=(245, 211, 167), width=max(2, round(0.4 * k)))
    T(rx + 3.2, by0 + 2.4, "Titul SIZNIKI ekanini tekshiring!", 4.0, True, (135, 68, 0))
    para(rx + 3.2, by0 + 8.6, "Titulning tepasidagi ID, xona va o'rin raqami sizniki bo'lishi shart. "
         "Boshqa o'quvchining tituli bo'lsa — to'ldirmasdan nazoratchiga ayting.", 3.3, rw - 6.4, (135, 68, 0))

    # --- Bo'yash misollari ---
    sy = cy0 + lay.h + 8.0                    # ~190 mm
    T(10, sy, "Doirachani qanday bo'yash kerak?", 5.2, True, INK)
    ex = [
        ("good", "To'liq bo'yalgan", "TO'G'RI"),
        ("half", "Yarim bo'yalgan", "XATO"),
        ("tick", "Galochka yoki «x»", "XATO"),
        ("two", "Ikkita belgi", "XATO"),
        ("out", "Doiradan chiqqan", "XATO"),
    ]
    cw = (210 - 20) / len(ex)
    rr = 5.2
    for i, (kind, cap, verdict) in enumerate(ex):
        x = 10 + i * cw
        good = verdict == "TO'G'RI"
        col = GREEN if good else RED
        d.rounded_rectangle([x * k, (sy + 8) * k, (x + cw - 3) * k, (sy + 46) * k], radius=2.5 * k,
                            fill=(235, 248, 241) if good else (253, 240, 239), outline=col, width=max(2, round(0.35 * k)))
        mx, my = x + (cw - 3) / 2, sy + 25

        def circle(px, py, rad=rr, fillc=None):
            d.ellipse([(px - rad) * k, (py - rad) * k, (px + rad) * k, (py + rad) * k], outline=(40, 40, 60), width=max(2, round(0.45 * k)), fill=fillc)
        dark = (25, 30, 60)
        if kind == "good":
            circle(mx, my, fillc=dark)
        elif kind == "half":
            circle(mx, my)
            d.pieslice([(mx - rr) * k, (my - rr) * k, (mx + rr) * k, (my + rr) * k], 90, 270, fill=dark)
        elif kind == "tick":
            circle(mx, my)
            d.line([((mx - 3) * k, (my + 0.3) * k), ((mx - 0.8) * k, (my + 3) * k), ((mx + 3.6) * k, (my - 3.6) * k)], fill=dark, width=round(0.9 * k))
        elif kind == "two":
            circle(mx - 6.2, my, 4.6, dark)
            circle(mx + 6.2, my, 4.6, dark)
        else:
            circle(mx, my)
            d.ellipse([(mx - 3.0) * k, (my - 8.8) * k, (mx + 7.5) * k, (my + 2.0) * k], fill=dark)
        # belgi: to'g'ri — galochka, xato — «x» (shrift glifiga bog'lanmaslik uchun chiziq bilan chiziladi)
        bx, by = x + (cw - 3) / 2, sy + 39
        d.ellipse([(bx - 3.2) * k, (by - 3.2) * k, (bx + 3.2) * k, (by + 3.2) * k], fill=col)
        w = max(2, round(0.7 * k))
        if good:
            d.line([((bx - 1.6) * k, (by + 0.1) * k), ((bx - 0.3) * k, (by + 1.5) * k), ((bx + 1.9) * k, (by - 1.5) * k)], fill="white", width=w)
        else:
            d.line([((bx - 1.4) * k, (by - 1.4) * k), ((bx + 1.4) * k, (by + 1.4) * k)], fill="white", width=w)
            d.line([((bx - 1.4) * k, (by + 1.4) * k), ((bx + 1.4) * k, (by - 1.4) * k)], fill="white", width=w)
        d.text((mx * k, (sy + 12.4) * k), cap, font=F(3.1, True), fill=INK, anchor="ma")

    # --- Qoidalar ---
    qy0 = sy + 52.0
    d.rounded_rectangle([10 * k, qy0 * k, 200 * k, (qy0 + 44) * k], radius=3 * k, fill=SOFT)
    T(14, qy0 + 3.5, "Esda tuting", 4.6, True, BLUE)
    rules = [
        "Qora yoki ko'k ruchka (yoki yumshoq qalam) ishlating; doirachani qalin va to'liq bo'yang.",
        "Ruchkadagi belgini o'chirib bo'lmaydi. Adashsangiz — nazoratchiga ayting. Qalam bo'lsa, o'chirgich bilan butunlay o'chiring.",
        "ID ni belgilamasangiz yoki xato belgilasangiz, natijangiz qo'shimcha tekshiruvga tushadi va e'lon qilinishi kechikadi.",
        "Titulga ism yozilmaydi — xolislik uchun faqat ID bilan ishlanadi. Titulni bukmang, ifloslamang.",
    ]
    yy = qy0 + 10.5
    for r in rules:
        d.ellipse([14 * k, (yy + 1.2) * k, 16.2 * k, (yy + 3.4) * k], fill=BLUE)
        yy = para(18.5, yy, r, 3.35, 176, INK) + 1.4
    T(105, 293.0, f"{olympiad}  ·  Titul to'ldirish qo'llanmasi", 2.8, False, MUTED, "ma")
    return img


@lru_cache(maxsize=4)
def guide_png(olympiad: str, dpi: int = 200) -> bytes:
    buf = io.BytesIO()
    render_guide(olympiad, dpi).save(buf, "PNG", optimize=True)
    return buf.getvalue()


@lru_cache(maxsize=4)
def guide_pdf(olympiad: str, dpi: int = 200) -> bytes:
    buf = io.BytesIO()
    render_guide(olympiad, dpi).save(buf, "PDF", resolution=dpi, quality=92)
    return buf.getvalue()

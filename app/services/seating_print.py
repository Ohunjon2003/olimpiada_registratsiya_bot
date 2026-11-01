"""Xona eshigiga osiladigan ro'yxat va o'tirish sxemasi (A4 portret sahifalar, Pillow)."""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from . import design as dz
from .seating import fmt_day, seat_number, weekday

DPI = 200
PW, PH = round(210 / 25.4 * DPI), round(297 / 25.4 * DPI)   # 1654 × 2339
M = round(12 / 25.4 * DPI)                                  # chekka: 12 mm


def _f(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return dz.pil_font(dz.STATIC_FONTS / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)


def _fit(d: ImageDraw.ImageDraw, text: str, font, max_w: float) -> str:
    """Sig'masa oxirini «…» bilan qisqartirish."""
    text = str(text)
    if d.textlength(text, font=font) <= max_w:
        return text
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _header(d: ImageDraw.ImageDraw, olympiad: str, sess, room, count: int, page: int, pages: int) -> int:
    y = M
    d.text((M, y), _fit(d, olympiad, _f(30), PW - 2 * M), font=_f(30), fill="#475569")
    y += 46
    b = room.building.name if room.building else ""
    title = f"Xona: {room.name}" + (f" ({room.floor}-qavat)" if room.floor else "")
    d.text((M, y), title, font=_f(64, True), fill="#0f172a")
    d.text((PW - M, y + 18), f"{page}/{pages}", font=_f(30), fill="#94a3b8", anchor="ra")
    y += 84
    d.text((M, y), _fit(d, b, _f(32), PW - 2 * M), font=_f(32), fill="#334155")
    y += 50
    line = (f"{sess.name} · {fmt_day(sess.day)} ({weekday(sess.day)}) · kelish {sess.arrive_time} · "
            f"boshlanish {sess.start_time} · {count} o'quvchi")
    d.text((M, y), _fit(d, line, _f(32, True), PW - 2 * M), font=_f(32, True), fill="#1e3a8a")
    y += 56
    d.line([M, y, PW - M, y], fill="#0f172a", width=3)
    return y + 18


def room_pages(items: list, olympiad: str) -> list[Image.Image]:
    """items: [(smena, xona, biriktirish, o'quvchi)] — bitta smena va xona uchun."""
    sess, room = items[0][0], items[0][1]
    items = sorted(items, key=lambda x: (x[2].row, x[2].desk, x[2].seat))
    rows_per_page = 34
    list_pages = max(1, (len(items) + rows_per_page - 1) // rows_per_page)
    pages_total = list_pages + 1
    out = []
    # Ustunlar kengligi (px, 200 dpi): oxirgisi — «Imzo» — qolgan joyni oladi (~260 px ≈ 3,3 sm)
    cols = [("№", 80), ("Joy", 180), ("ID", 110), ("Familiya Ism", 470), ("Maktab", 290), ("Sinf", 80), ("Imzo", 0)]
    for pi in range(list_pages):
        img = Image.new("L", (PW, PH), 255)
        d = ImageDraw.Draw(img)
        y = _header(d, olympiad, sess, room, len(items), pi + 1, pages_total)
        x = M
        widths = [w for _, w in cols]
        widths[-1] = PW - 2 * M - sum(widths[:-1])
        rh = 56
        top = y
        d.rectangle([M, y, PW - M, y + rh], fill=225)
        for (name, _), w in zip(cols, widths):
            d.text((x + 10, y + rh / 2), name, font=_f(28, True), fill=0, anchor="lm")
            x += w
        y += rh
        for sess_, room_, a, p in items[pi * rows_per_page:(pi + 1) * rows_per_page]:
            x = M
            vals = [str(seat_number(room, (a.row, a.desk, a.seat))), f"{a.row}-q, {a.desk}-p" + (f", {a.seat}" if room.seats_per_desk > 1 else ""),
                    p.code or "", p.full_name, p.school.name if p.school else "", p.grade.name if p.grade else "", ""]
            for i, (v, w) in enumerate(zip(vals, widths)):
                font = _f(30, i in (0, 3))
                d.text((x + 10, y + rh / 2), _fit(d, v, font, w - 20), font=font, fill=0, anchor="lm")
                x += w
            y += rh
            d.line([M, y, PW - M, y], fill=200, width=1)
        x = M
        for w in widths[:-1]:  # ustun chiziqlari
            x += w
            d.line([x, top, x, y], fill=200, width=1)
        d.rectangle([M, top, PW - M, y], outline=120, width=2)
        out.append(img.convert("RGB"))
    out.append(chart_page(items, olympiad, pages_total))
    return out


def chart_page(items: list, olympiad: str, pages_total: int) -> Image.Image:
    """O'tirish sxemasi: doska tepada, har o'rin — raqam, ID va familiya."""
    sess, room = items[0][0], items[0][1]
    by_seat = {(a.row, a.desk, a.seat): p for _, _, a, p in items}
    img = Image.new("RGB", (PW, PH), "white")
    d = ImageDraw.Draw(img)
    y = _header(d, olympiad, sess, room, len(items), pages_total, pages_total)
    d.rounded_rectangle([PW / 2 - 300, y, PW / 2 + 300, y + 60], radius=12, fill="#334155")
    d.text((PW / 2, y + 30), "DOSKA", font=_f(32, True), fill="white", anchor="mm")
    y += 90
    S = room.seats_per_desk
    gap = 40                                     # partalar orasidagi yo'lak
    avail_w = PW - 2 * M - gap * (room.desks - 1)
    seat_w = avail_w / (room.desks * S)
    avail_h = PH - y - M - 40
    row_gap = 26
    seat_h = min(240, (avail_h - row_gap * (room.rows - 1)) / room.rows)
    fs = max(16, min(30, int(seat_w / 7)))
    for r in range(1, room.rows + 1):
        for dsk in range(1, room.desks + 1):
            for s in range(1, S + 1):
                x0 = M + (dsk - 1) * (S * seat_w + gap) + (s - 1) * seat_w
                y0 = y + (r - 1) * (seat_h + row_gap)
                box = [x0 + 3, y0, x0 + seat_w - 3, y0 + seat_h]
                p = by_seat.get((r, dsk, s))
                d.rounded_rectangle(box, radius=10, outline="#94a3b8", width=2, fill="#f1f5f9" if p else "white")
                no = seat_number(room, (r, dsk, s))
                d.text((x0 + 12, y0 + 8), str(no), font=_f(fs, True), fill="#1e3a8a")
                if p:
                    d.text((x0 + seat_w - 12, y0 + 8), p.code or "", font=_f(fs), fill="#475569", anchor="ra")
                    d.text((x0 + 12, y0 + seat_h * 0.45), _fit(d, p.last_name, _f(fs, True), seat_w - 24), font=_f(fs, True), fill="#0f172a")
                    d.text((x0 + 12, y0 + seat_h * 0.45 + fs * 1.25), _fit(d, p.first_name, _f(fs), seat_w - 24), font=_f(fs), fill="#334155")
    d.text((M, PH - M), f"Partalar: {room.rows} qator × {room.desks} ta, partada {S} o'rin. O'rin raqami — chap yuqori burchakda.",
           font=_f(24), fill="#64748b", anchor="ld")
    return img

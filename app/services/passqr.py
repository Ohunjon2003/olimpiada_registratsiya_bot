"""Ruxsatnomadagi davomat QR kodi.

Matn: «OP1.<o'quvchi id>.<tur id>.<imzo>» — masalan «OP1.127.1.K3F9QZ».
  * Faqat katta harf, raqam va nuqta → QR «alphanumeric» rejimida, 1-versiya (21×21 katak):
    kataklar yirik, eski telefon kamerasi ham uzoqdan tez o'qiydi.
  * Imzo — SECRET_KEY bilan HMAC: QRni qo'lda yasab (soxta ruxsatnoma) davomat belgilab bo'lmaydi.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Optional

from ..config import config

PREFIX = "OP1"
_RX = re.compile(r"^OP1\.(\d{1,7})\.(\d{1,5})\.([A-Z2-7]{6})$")


def _sig(pid: int, rid: int) -> str:
    mac = hmac.new(config.SECRET_KEY.encode(), f"pass-qr|{pid}|{rid}".encode(), hashlib.sha256).digest()
    return base64.b32encode(mac).decode()[:6]          # 30 bit — taxmin qilib topib bo'lmaydi


def make(pid: int, rid: int) -> str:
    return f"{PREFIX}.{pid}.{rid}.{_sig(pid, rid)}"


@dataclass
class Parsed:
    pid: int
    rid: int


def parse(text: str) -> Optional[Parsed]:
    """QR matnini tekshiradi. Imzo noto'g'ri yoki format boshqa bo'lsa — None."""
    m = _RX.match((text or "").strip().upper())
    if not m:
        return None
    pid, rid, sig = int(m.group(1)), int(m.group(2)), m.group(3)
    if not hmac.compare_digest(sig, _sig(pid, rid)):
        return None
    return Parsed(pid, rid)


def matrix(text: str) -> list[list[bool]]:
    """QR kataklari (True — qora). Atrofidagi oq hoshiya kiritilmagan."""
    import cv2
    params = cv2.QRCodeEncoder.Params()
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_M   # 15% shikastlanishga chidamli
    img = cv2.QRCodeEncoder.create(params).encode(text)            # 1 piksel = 1 katak, 2 katak hoshiya bilan
    dark = img < 128
    rows = [i for i in range(dark.shape[0]) if dark[i].any()]
    cols = [j for j in range(dark.shape[1]) if dark[:, j].any()]
    sub = dark[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    return [[bool(v) for v in row] for row in sub]


def draw(size_px: int, text: str, color: tuple = (0, 0, 0)):
    """Kvadrat QR rasm (RGBA): 4 katak oq hoshiya bilan, kataklar butun pikselga tekislangan (xira bo'lmaydi)."""
    from PIL import Image, ImageDraw
    m = matrix(text)
    n = len(m) + 8                                  # 4 katak hoshiya har tomondan (standart)
    cell = max(1, size_px // n)
    side = cell * n
    img = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    for i, row in enumerate(m):
        for j, v in enumerate(row):
            if v:
                x, y = (j + 4) * cell, (i + 4) * cell
                d.rectangle([x, y, x + cell - 1, y + cell - 1], fill=color + (255,))
    if side != size_px:                             # kerakli o'lchamga — faqat «eng yaqin» usulida
        img = img.resize((size_px, size_px), Image.NEAREST)
    return img

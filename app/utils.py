"""Umumiy yordamchi funksiyalar: vaqt, transliteratsiya, telefon, ism tekshiruvi."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Vaqt
# ---------------------------------------------------------------------------
def now_utc() -> datetime:
    """Joriy UTC vaqt (tzinfo siz) — bazada shunday saqlanadi."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _tz():
    from .config import config  # aylanma importdan qochish uchun
    return config.TZ


def to_local(dt: Optional[datetime]) -> Optional[datetime]:
    """UTC vaqtni Toshkent vaqtiga o'tkazadi."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(_tz())


def fmt_dt(dt: Optional[datetime], fmt: str = "%d.%m.%Y %H:%M") -> str:
    loc = to_local(dt)
    return loc.strftime(fmt) if loc else ""


def parse_local_input(value: str) -> Optional[datetime]:
    """'2026-11-01T09:00' (datetime-local) → UTC naive. Bo'sh bo'lsa None."""
    value = (value or "").strip()
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def to_local_input(dt: Optional[datetime]) -> str:
    """UTC → datetime-local input qiymati."""
    loc = to_local(dt)
    return loc.strftime("%Y-%m-%dT%H:%M") if loc else ""


# ---------------------------------------------------------------------------
# Lotin ↔ Kirill transliteratsiya (o'zbek tili)
# ---------------------------------------------------------------------------
_APOS = "'ʻʼ‘’`"
_SINGLE = {
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ",
    "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
    "x": "х", "y": "й", "z": "з", "c": "ц", "w": "в",
}
_VOWELS = set("aeiouAEIOU")
# Tarjima qilinmaydigan qismlar: {placeholder}, <html teg>, havolalar, @username
_PROTECT = re.compile(r"(\{[^}]*\}|<[^>]*>|https?://\S+|t\.me/\S+|@\w+|(?<![\w/])/[A-Za-z_]+)")


def _case(src: str, dst: str) -> str:
    """Manba harf katta bo'lsa natijani ham katta qiladi."""
    if src[:1].isupper():
        # 'SH' → 'Ш', 'Sh' → 'Ш'
        return dst.upper() if len(dst) == 1 or src.isupper() else dst[0].upper() + dst[1:]
    return dst


def _lat2cyr_plain(text: str) -> str:
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        low = ch.lower()
        nxt = text[i + 1] if i + 1 < n else ""
        nxt_low = nxt.lower()
        prev = text[i - 1] if i > 0 else ""
        word_start = not prev.isalpha() and not (prev and prev in _APOS)

        # o' va g' (tutuq belgisi bilan)
        if low in ("o", "g") and nxt and nxt in _APOS:
            out.append(_case(ch, "ў" if low == "o" else "ғ"))
            i += 2
            continue
        # sh, ch
        if low in ("s", "c") and nxt_low == "h":
            out.append(_case(ch, "ш" if low == "s" else "ч"))
            i += 2
            continue
        # yo, yu, ya, ye (lekin "yo'" → "йў")
        if low == "y" and nxt_low in ("o", "u", "a", "e"):
            after = text[i + 2] if i + 2 < n else ""
            if not (nxt_low == "o" and after and after in _APOS):
                out.append(_case(ch, {"o": "ё", "u": "ю", "a": "я", "e": "е"}[nxt_low]))
                i += 2
                continue
        # e — so'z boshida э, aks holda е
        if low == "e":
            out.append(_case(ch, "э" if word_start else "е"))
            i += 1
            continue
        # tutuq belgisi (ma'lumot → маълумот)
        if ch and ch in _APOS:
            out.append("ъ" if prev.isalpha() else ch)
            i += 1
            continue
        if low in _SINGLE:
            out.append(_case(ch, _SINGLE[low]))
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def lat2cyr(text: str) -> str:
    """O'zbek lotin matnini kirillga o'giradi ({..}, <..>, havolalarni saqlaydi)."""
    if not text:
        return text
    parts = _PROTECT.split(text)
    return "".join(p if _PROTECT.fullmatch(p) else _lat2cyr_plain(p) for p in parts)


_CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "x", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "'", "ы": "i", "ь": "", "э": "e", "ю": "yu",
    "я": "ya", "ў": "o'", "қ": "q", "ғ": "g'", "ҳ": "h",
}


def cyr2lat(text: str) -> str:
    """Kirill → lotin (asosan qidiruv uchun)."""
    out = []
    for ch in text or "":
        low = ch.lower()
        if low in _CYR2LAT:
            rep = _CYR2LAT[low]
            out.append(rep.capitalize() if ch.isupper() and rep else rep)
        else:
            out.append(ch)
    return "".join(out)


def search_norm(text: str) -> str:
    """Qidiruv uchun: kirillni lotinga, tutuq belgilarini olib tashlab, kichik harf."""
    t = cyr2lat(text or "").lower()
    for a in _APOS:
        t = t.replace(a, "")
    return re.sub(r"\s+", " ", t).strip()


def by_lang(lat: str, cyr: str, lang: str) -> str:
    """Til bo'yicha matn: kirill bo'sh bo'lsa lotindan avtomatik o'giradi."""
    if lang == "cyr":
        return cyr.strip() if cyr and cyr.strip() else lat2cyr(lat or "")
    return lat or ""


# ---------------------------------------------------------------------------
# Tekshiruvlar
# ---------------------------------------------------------------------------
def normalize_phone(raw: str) -> Optional[str]:
    """Telefonni +998XXXXXXXXX ko'rinishiga keltiradi; noto'g'ri bo'lsa None.

    Qabul qilinadi: +998 90 123 45 67, 998901234567, 90-123-45-67, (90) 1234567
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) == 12 and digits.startswith("998"):
        # Operator kodi 2 xonali va 0 bilan boshlanmaydi (masalan 90, 91, 33, 99, 77)
        if digits[3] == "0":
            return None
        return "+" + digits
    return None


def format_phone(phone: str) -> str:
    """+998901234567 → +998 90 123 45 67"""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 12:
        return f"+{d[:3]} {d[3:5]} {d[5:8]} {d[8:10]} {d[10:]}"
    return phone or ""


_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ'\- ]+$")


def clean_name(raw: str) -> Optional[str]:
    """Ism/familiyani tekshiradi va tartibga soladi. Noto'g'ri bo'lsa None.

    Qoidalar: 2–40 belgi, faqat harflar, tutuq belgisi, chiziqcha va bo'shliq,
    kamida 2 ta harf, raqam va emoji yo'q.
    """
    if not raw:
        return None
    s = raw.strip()
    for a in "ʻʼ‘’`":
        s = s.replace(a, "'")
    s = re.sub(r"\s+", " ", s)
    if not (2 <= len(s) <= 40) or not _NAME_RE.match(s):
        return None
    if sum(c.isalpha() for c in s) < 2:
        return None
    # Hammasi kichik yoki hammasi katta yozilgan bo'lsa — har bo'lakning birinchi harfini katta qilamiz
    letters = [c for c in s if c.isalpha()]
    if all(c.islower() for c in letters) or all(c.isupper() for c in letters):
        def cap(word: str) -> str:
            return word[:1].upper() + word[1:].lower()
        s = " ".join("-".join(cap(p) for p in w.split("-")) for w in s.split(" "))
    return s


def fmt_num(x: float) -> str:
    """12.0 → '12', 12.5 → '12.5'"""
    if x is None or x == "":
        return ""
    try:
        x = round(float(x), 2)
    except (TypeError, ValueError):
        return str(x)
    return str(int(x)) if x == int(x) else f"{x:g}"

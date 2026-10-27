"""Admin: bot matnlari (savollar, namunalar, tugmalar), salomlashish/tabrik va stikerlar."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...bot import stickers
from ...bot import texts as T
from ...db import get_session
from ...models import User
from ...services import audit
from ...services import settings as st
from ..core import client_ip, flash, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/texts", dependencies=[Depends(verify_csrf)])

# Telegram HTML rejimida ruxsat etilgan teglar
_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre",
                 "a", "tg-spoiler", "blockquote", "span"}
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)([^<>]*)>")
_AMP_RE = re.compile(r"&(?!(?:[a-zA-Z]+|#\d+|#x[0-9a-fA-F]+);)")
_LIMITS = {"button": 60, "plain": 100, "html": 2000}
_MAIN_TEXTS = [("welcome_text", "👋 Salomlashish matni", "{olympiad} — olimpiada nomi"),
               ("congrats_text", "🎉 Ro'yxatdan o'tgach chiqadigan tabrik va reklama matni",
                "Telefon, havolalar, qisqa reklama. ID raqami alohida xabarda yuboriladi.")]
_SLOT_LABELS = {"sticker_welcome": "👋 Salomlashishda (/start)",
                "sticker_congrats": "🎉 Ro'yxatdan o'tib bo'lgach",
                "sticker_results": "🏆 Natijalar e'lon qilinganda"}


def fix_html(text: str) -> tuple[str, Optional[str]]:
    """HTML matnni tekshiradi. Yolg'iz & va < belgilarini avtomatik xavfsiz qiladi.

    Qaytaradi: (tuzatilgan matn, xato yoki None).
    """
    text = _AMP_RE.sub("&amp;", text)
    out, stack, pos = [], [], 0
    for m in _TAG_RE.finditer(text):
        out.append(text[pos:m.start()].replace("<", "&lt;"))
        closing, tag = m.group(1) == "/", m.group(2).lower()
        if tag not in _ALLOWED_TAGS:
            # Ruxsat etilmagan teg — oddiy matn sifatida ko'rsatamiz
            out.append(m.group(0).replace("<", "&lt;"))
        elif closing:
            if not stack or stack[-1] != tag:
                return text, f"</{tag}> tegi ochilmagan yoki noto'g'ri tartibda yopilgan"
            stack.pop()
            out.append(m.group(0))
        else:
            stack.append(tag)
            out.append(m.group(0))
        pos = m.end()
    out.append(text[pos:].replace("<", "&lt;"))
    if stack:
        return text, f"<{stack[-1]}> tegi yopilmagan (</{stack[-1]}> qo'shing)"
    return "".join(out), None


def check_text(key: str, value: str) -> tuple[str, Optional[str]]:
    """Admin kiritgan matnni tekshiradi va tozalaydi."""
    kind = T.kind(key)
    # Boshidagi yangi qator saqlanadi (ba'zi matnlar "\n" bilan boshlanadi), oxiridagi bo'shliq olib tashlanadi
    value = value.replace("\r\n", "\n").rstrip()
    if not value.strip():
        return "", None
    if kind != "html":
        value = value.strip()
    if len(value) > _LIMITS[kind]:
        return value, f"juda uzun ({len(value)} belgi, ko'pi bilan {_LIMITS[kind]})"
    if kind == "button":
        if "\n" in value:
            return value, "tugma matni bir qatorda bo'lishi kerak"
    elif kind == "plain":
        if "\n" in value or any(c in value for c in "<>&"):
            return value, "bir qatorda bo'lsin va < > & belgilari ishlatilmasin"
    else:
        value, err = fix_html(value)
        if err:
            return value, err
    need = T.placeholders(key)
    if need:
        found = set(re.findall(r"\{(\w+)\}", value))
        if need - found:
            return value, "quyidagilar bo'lishi shart: " + ", ".join("{" + p + "}" for p in sorted(need - found))
        try:
            value.format(**{p: "x" for p in need})
        except (KeyError, IndexError):
            return value, ("faqat shu o'zgaruvchilar ishlatiladi: " + ", ".join("{" + p + "}" for p in sorted(need)))
        except ValueError:
            return value, "{ yoki } belgisi noto'g'ri ishlatilgan"
    return value, None


def _items(values: Optional[dict] = None, errors: Optional[dict] = None) -> list:
    """Sahifa uchun guruhlangan ro'yxat."""
    values, errors = values or {}, errors or {}
    groups = []
    for title, keys in T.GROUPS:
        rows = []
        for key, label in keys:
            lat = values.get(key, st.raw(f"txt.{key}") or T.default(key))
            cyr = values.get(key + ".cyr", st.raw(f"txt.{key}.cyr"))
            rows.append({
                "key": key, "label": label, "kind": T.kind(key),
                "lat": lat, "cyr": cyr,
                "default": T.default(key), "cyr_auto": T.t(key, "cyr") if not cyr else "",
                "changed": bool(st.raw(f"txt.{key}") or st.raw(f"txt.{key}.cyr")),
                "placeholders": sorted(T.placeholders(key)),
                "error": errors.get(key),
            })
        groups.append({"title": title, "rows": rows,
                       "has_error": any(r["error"] for r in rows),
                       "changed": sum(r["changed"] for r in rows)})
    return groups


def _slots() -> list:
    out = []
    for slot in stickers.SLOTS:
        data = stickers.parse(st.get(slot) or "")
        out.append({"key": slot, "label": _SLOT_LABELS[slot], "data": data})
    return out


def _page(request: Request, values=None, errors=None, main_values=None, main_errors=None, status=200):
    from ...bot import loader
    return render(request, "admin/texts.html", {
        "groups": _items(values, errors),
        "main_texts": [{"key": k, "label": label, "help": help_,
                        "lat": (main_values or {}).get(k, st.get(k)),
                        "cyr": (main_values or {}).get(k + "_cyr", st.get(k + "_cyr")),
                        "error": (main_errors or {}).get(k)} for k, label, help_ in _MAIN_TEXTS],
        "slots": _slots(), "recent": stickers.recent(), "bot_on": loader.bot is not None,
    }, status)


@router.get("")
async def texts_page(request: Request, user: User = Depends(require_admin)):
    return _page(request)


@router.post("")
async def texts_save(request: Request, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    form = await request.form()
    values, errors, to_save = {}, {}, {}
    for _, keys in T.GROUPS:
        for key, label in keys:
            for suffix in ("", ".cyr"):
                name = key + suffix
                if name not in form:
                    continue
                raw = str(form.get(name) or "")
                clean, err = check_text(key, raw)
                values[name] = raw
                if err:
                    errors[key] = ("Kirill: " if suffix else "") + err
                    continue
                if suffix == "" and clean.strip() == T.default(key).strip():
                    clean = ""  # standart matn bilan bir xil — alohida saqlamaymiz
                to_save[f"txt.{name}"] = clean

    # Tugmalar bir-biridan farq qilishi kerak (aks holda bot qaysi tugma bosilganini ajrata olmaydi)
    for lang in ("lat", "cyr"):
        seen: dict[str, str] = {}
        for _, keys in T.GROUPS:
            for key, label in keys:
                if T.kind(key) != "button" or key in ("btn_lat", "btn_cyr") or key in errors:
                    continue
                lat = to_save.get(f"txt.{key}") or T.default(key)
                val = (to_save.get(f"txt.{key}.cyr") or T._to_cyr(lat)) if lang == "cyr" else lat
                if val in seen:
                    errors[key] = f"«{val}» tugmasi «{seen[val]}» bilan bir xil bo'lib qoldi"
                seen[val] = label

    if errors:
        flash(request, f"{len(errors)} ta matnda xato bor — qizil bilan belgilangan. Hech narsa saqlanmadi.", "error")
        return _page(request, values, errors, status=400)

    changed = [k[4:] for k, v in to_save.items() if st.raw(k) != v]
    await st.set_many(session, to_save)
    if changed:
        audit.log(session, user, "update", "texts", "", "bot matnlari: " + ", ".join(changed[:20]),
                  ip=client_ip(request))
        await session.commit()
    flash(request, f"Saqlandi ({len(changed)} ta matn o'zgardi). Botda darhol kuchga kirdi." if changed
          else "O'zgarish yo'q")
    return RedirectResponse("/admin/texts", 303)


@router.post("/main")
async def main_texts_save(request: Request, user: User = Depends(require_admin),
                          session: AsyncSession = Depends(get_session)):
    """Salomlashish va tabrik matnlari (oddiy matn — HTML'siz yuboriladi)."""
    form = await request.form()
    vals = {}
    for key, _, _ in _MAIN_TEXTS:
        for k in (key, key + "_cyr"):
            vals[k] = str(form.get(k) or "").replace("\r\n", "\n").strip()[:3500]
    old = {k: st.get(k) for k in vals}
    await st.set_many(session, vals)
    changed = {k: v for k, v in vals.items() if old[k] != v}
    if changed:
        audit.log(session, user, "update", "settings", "", ", ".join(changed), new=changed, ip=client_ip(request))
        await session.commit()
    flash(request, "Saqlandi")
    return RedirectResponse("/admin/texts#main", 303)


@router.post("/sticker")
async def sticker_save(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    """Stiker joyini o'rnatish: emoji, botga yuborilgan stiker yoki o'chirish."""
    form = await request.form()
    slot = str(form.get("slot") or "")
    if slot not in stickers.SLOTS:
        raise HTTPException(400, "Noto'g'ri joy")
    action = str(form.get("action") or "")
    if action == "emoji":
        value = str(form.get("emoji") or "").strip()
        if len(value) > 16 or "\n" in value:
            flash(request, "Bu yerga faqat bitta-ikkita emoji yozing (masalan 🎉)", "error")
            return RedirectResponse("/admin/texts#stickers", 303)
    elif action == "sticker":
        fid = str(form.get("file_id") or "")
        item = next((x for x in stickers.recent() if x["file_id"] == fid), None)
        if item is None:
            flash(request, "Stiker topilmadi — uni botga qaytadan yuboring", "error")
            return RedirectResponse("/admin/texts#stickers", 303)
        value = stickers.make_sticker_value(item["file_id"], item["preview"], item["emoji"])
    else:
        value = ""
    await st.set_many(session, {slot: value})
    audit.log(session, user, "update", "settings", slot, f"{slot}: {action or 'off'}", ip=client_ip(request))
    await session.commit()
    flash(request, "Stiker saqlandi" if value else "O'chirildi")
    return RedirectResponse("/admin/texts#stickers", 303)


_preview_cache: dict[str, tuple[bytes, str]] = {}


def _mime(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    return "application/octet-stream"


@router.get("/sticker-preview")
async def sticker_preview(f: str, user: User = Depends(require_admin)):
    """Stiker rasmini Telegram'dan olib ko'rsatadi (keshlanadi)."""
    from ...bot import loader
    if f not in _preview_cache:
        if loader.bot is None:
            raise HTTPException(404)
        try:
            file = await loader.bot.get_file(f)
            buf = await loader.bot.download_file(file.file_path)
            data = buf.read()
        except Exception:
            raise HTTPException(404, "Rasm olinmadi")
        if len(_preview_cache) > 200:
            _preview_cache.clear()
        _preview_cache[f] = (data, _mime(data))
    data, mime = _preview_cache[f]
    if not mime.startswith("image/"):
        raise HTTPException(404)
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})

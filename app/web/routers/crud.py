"""Ma'lumotnomalar uchun umumiy CRUD: maktablar, sinflar, fanlar, bloklar,
turlar, kanallar, nizom va qo'shimcha savollar.

Har bir bo'lim `Resource` bilan tavsiflanadi: maydonlar, tekshiruvlar va
saqlashdan keyingi amallar. Ro'yxat/forma sahifalari bitta shablonda.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ...config import config
from ...db import get_session
from ...models import (Block, Channel, CustomQuestion, Grade, Participant,
                       Result, Round, Rule, School, ScoreGroup, Subject, User,
                       participant_subjects, round_participants)
from ...services import audit, ranking
from ..core import client_ip, flash, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/c", dependencies=[Depends(verify_csrf)])


# ---------------------------------------------------------------------------
# Tavsiflar
# ---------------------------------------------------------------------------
@dataclass
class Field:
    name: str
    label: str
    type: str = "text"   # text|textarea|int|float|bool|select|multiselect|file|url
    required: bool = False
    help: str = ""
    options: Any = None   # select uchun: [(qiymat, nom)] yoki async funksiya(session)
    rel_model: Any = None  # multiselect uchun model
    default: Any = None
    placeholder: str = ""
    rows: int = 3
    group: str = ""        # JS bilan ko'rsatish/yashirish uchun (fan rejimlari)


@dataclass
class Resource:
    key: str
    title: str
    singular: str
    model: Any
    fields: list[Field]
    columns: list[tuple[str, Callable[[Any], Any]]]
    help: str = ""
    bulk: bool = False                 # ro'yxatni ko'p qatorli matndan qo'shish
    validate: Optional[Callable[[AsyncSession, dict, Any], Awaitable[list[str]]]] = None
    after_save: Optional[Callable[[AsyncSession, Any, Any, bool], Awaitable[Optional[str]]]] = None
    before_delete: Optional[Callable[[AsyncSession, Any], Awaitable[Optional[str]]]] = None
    extra_template: str = ""
    row_actions: list[tuple[str, str]] = field(default_factory=list)  # (nom, url shabloni {id})

    @property
    def has_position(self) -> bool:
        return hasattr(self.model, "position")


def _cyr_help(what: str = "nom") -> str:
    return f"Bo'sh qoldirilsa, {what} lotinchadan kirillga avtomatik o'giriladi."


# --- Tekshiruvlar va hooklar ---------------------------------------------------
async def _count(session, q) -> int:
    return (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()


async def school_delete(session, obj) -> Optional[str]:
    n = await _count(session, select(Participant.id).where(Participant.school_id == obj.id))
    return f"Bu maktabga {n} ta ishtirokchi bog'langan. O'chirish o'rniga nofaol qiling." if n else None


async def grade_delete(session, obj) -> Optional[str]:
    n = await _count(session, select(Participant.id).where(Participant.grade_id == obj.id))
    return f"Bu sinfda {n} ta ishtirokchi bor. O'chirish o'rniga nofaol qiling." if n else None


async def subject_delete(session, obj) -> Optional[str]:
    n = await _count(session, select(participant_subjects.c.participant_id)
                     .where(participant_subjects.c.subject_id == obj.id))
    if n:
        return f"Bu fanni {n} ta ishtirokchi tanlagan. O'chirish o'rniga nofaol qiling."
    r = await _count(session, select(Result.id).where(Result.subject_id == obj.id))
    return f"Bu fan bo'yicha {r} ta natija bor." if r else None


async def round_delete(session, obj) -> Optional[str]:
    r = await _count(session, select(Result.id).where(Result.round_id == obj.id))
    if r:
        return f"Bu turda {r} ta natija bor. Avval natijalarni o'chiring yoki turni nofaol qiling."
    total = await _count(session, select(Round.id))
    return "Kamida bitta tur qolishi kerak." if total <= 1 else None


async def subject_validate(session, data: dict, obj) -> list[str]:
    errs = []
    mode = data.get("scoring_mode")
    if mode == "uniform":
        if not data.get("total_questions") or data["total_questions"] <= 0:
            errs.append("Savollar soni 0 dan katta bo'lishi kerak")
        if not data.get("points_per_question") or data["points_per_question"] <= 0:
            errs.append("Bitta savol bali 0 dan katta bo'lishi kerak")
    elif mode == "manual":
        if not data.get("max_score_manual") or data["max_score_manual"] <= 0:
            errs.append("Maksimal ball 0 dan katta bo'lishi kerak")
    return errs


async def subject_after_save(session, obj: Subject, form, created: bool) -> Optional[str]:
    """Ball guruhlarini formadan saqlaydi (groups rejimi)."""
    if obj.scoring_mode != "groups":
        return None
    names = form.getlist("g_name")
    counts = form.getlist("g_count")
    points = form.getlist("g_points")
    ids = form.getlist("g_id")
    new_groups = []
    for i, name in enumerate(names):
        name = (name or "").strip()
        try:
            cnt = int(counts[i])
            pts = float(str(points[i]).replace(",", "."))
        except (ValueError, IndexError):
            return f"{i + 1}-guruh: savollar soni va bal son bo'lishi kerak"
        if not name and not cnt:
            continue
        if cnt <= 0 or pts <= 0:
            return f"«{name or i + 1}» guruh: savollar soni va bal 0 dan katta bo'lishi kerak"
        gid = int(ids[i]) if i < len(ids) and str(ids[i]).isdigit() else None
        new_groups.append((gid, name or f"{i + 1}-guruh", cnt, pts))
    if not new_groups:
        return "«Har xil ball» rejimi uchun kamida bitta guruh kiriting"
    # Mavjud guruhlarni yangilaymiz (id saqlanadi — kiritilgan natijalar buzilmasin)
    existing = {g.id: g for g in obj.groups}
    keep = []
    for pos, (gid, name, cnt, pts) in enumerate(new_groups):
        g = existing.get(gid) if gid else None
        if g is None:
            g = ScoreGroup(name=name, question_count=cnt, points=pts, position=pos)
            obj.groups.append(g)
        else:
            g.name, g.question_count, g.points, g.position = name, cnt, pts, pos
        keep.append(g)
    for g in list(obj.groups):
        if g not in keep:
            obj.groups.remove(g)
    return None


async def round_after_save(session, obj: Round, form, created: bool) -> Optional[str]:
    if obj.is_current:
        await session.execute(update(Round).where(Round.id != obj.id).values(is_current=False))
    return None


async def block_validate(session, data: dict, obj) -> list[str]:
    return [] if data.get("subjects") else ["Blokka kamida bitta fan tanlang"]


async def channel_validate(session, data: dict, obj) -> list[str]:
    errs = []
    url = data.get("url", "")
    if not re.match(r"^https?://", url or ""):
        errs.append("Havola https:// bilan boshlanishi kerak (masalan https://t.me/kanal)")
    if data.get("kind") == "telegram" and data.get("verify") and not data.get("chat_id"):
        errs.append("Telegram kanal obunasini tekshirish uchun «Kanal ID» kiriting (@kanal yoki -100...)")
    cid = data.get("chat_id") or ""
    if cid and not re.match(r"^(@[A-Za-z0-9_]{4,}|-100\d+|-?\d+)$", cid):
        errs.append("Kanal ID noto'g'ri: @kanal_nomi yoki -1001234567890 ko'rinishida bo'lsin")
    return errs


async def question_validate(session, data: dict, obj) -> list[str]:
    errs = []
    if data.get("qtype") in ("choice", "multichoice"):
        opts = [o for o in (data.get("options") or "").splitlines() if o.strip()]
        if len(opts) < 2:
            errs.append("Tanlov savoli uchun kamida 2 ta variant yozing (har qatorda bittadan)")
        if any(len(o) > 60 for o in opts):
            errs.append("Variant matni 60 belgidan oshmasin (Telegram tugmasi uchun)")
    if (data.get("min_len") or 0) > (data.get("max_len") or 200):
        errs.append("Minimal uzunlik maksimaldan katta bo'lmasin")
    lo, hi = data.get("min_value"), data.get("max_value")
    if lo is not None and hi is not None and lo > hi:
        errs.append("Minimal qiymat maksimaldan katta bo'lmasin")
    return errs


async def rule_after_save(session, obj: Rule, form, created: bool) -> Optional[str]:
    """Nizom faylini saqlash/o'chirish."""
    if form.get("remove_file") and obj.file_path:
        try:
            Path(obj.file_path).unlink(missing_ok=True)
        except OSError:
            pass
        obj.file_path = obj.file_name = obj.tg_file_id = ""
    up = form.get("file")
    if isinstance(up, UploadFile) and up.filename:
        data = await up.read()
        if len(data) > 45 * 1024 * 1024:
            return "Fayl 45 MB dan katta bo'lmasin (Telegram cheklovi)"
        await session.flush()  # id kerak
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", up.filename)[-80:]
        path = config.UPLOAD_DIR / "rules" / f"{obj.id}_{safe}"
        path.write_bytes(data)
        if obj.file_path and obj.file_path != str(path):
            Path(obj.file_path).unlink(missing_ok=True)
        obj.file_path, obj.file_name, obj.tg_file_id = str(path), up.filename[:200], ""
    if not (obj.text or "").strip() and not obj.file_path:
        return "Nizom uchun matn yoki fayl kiriting"
    return None


async def _subject_options(session):
    rows = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    return [(s.id, s.name + ("" if s.is_active else " (nofaol)")) for s in rows]


async def _grade_options(session):
    rows = (await session.execute(select(Grade).order_by(Grade.position, Grade.id))).scalars().all()
    return [(g.id, g.name) for g in rows]


def _yes(v: bool) -> str:
    return "✅" if v else "—"


MODES = [("uniform", "Bir xil ball (savollar soni × bal)"),
         ("groups", "Har xil ball (savol guruhlari)"),
         ("manual", "Ballni hakam o'zi kiritadi")]

RESOURCES: dict[str, Resource] = {}


def _register(res: Resource) -> None:
    RESOURCES[res.key] = res


_register(Resource(
    key="schools", title="Maktablar", singular="maktab", model=School, bulk=True,
    help="O'quvchilar ro'yxatdan o'tishda maktabni shu ro'yxatdan tanlaydi.",
    fields=[Field("name", "Nomi", required=True, placeholder="5-maktab"),
            Field("name_cyr", "Nomi (kirill)", help=_cyr_help()),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Nomi", lambda o: o.name), ("Kirill", lambda o: o.name_cyr or "avto")],
    before_delete=school_delete))

_register(Resource(
    key="grades", title="Sinflar", singular="sinf", model=Grade, bulk=True,
    help="Sinf nomini faqat raqam bilan yozish tavsiya etiladi: 5, 6, ... 11.",
    fields=[Field("name", "Nomi", required=True, placeholder="9"),
            Field("name_cyr", "Nomi (kirill)", help=_cyr_help()),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Nomi", lambda o: o.name)],
    before_delete=grade_delete))

_register(Resource(
    key="subjects", title="Fanlar", singular="fan", model=Subject,
    help="Har fan uchun baholash usulini tanlang. Sinflar tanlanmasa fan barcha sinflarga ko'rinadi.",
    fields=[Field("name", "Nomi", required=True, placeholder="Matematika"),
            Field("name_cyr", "Nomi (kirill)", help=_cyr_help()),
            Field("grades", "Qaysi sinflar uchun", "multiselect", options=_grade_options, rel_model=Grade,
                  help="Hech biri tanlanmasa — barcha sinflar"),
            Field("scoring_mode", "Baholash usuli", "select", required=True, options=MODES, default="uniform"),
            Field("total_questions", "Savollar soni", "int", default=30, group="uniform"),
            Field("points_per_question", "Bitta savol bali", "float", default=1, group="uniform"),
            Field("max_score_manual", "Maksimal ball", "float", default=100, group="manual"),
            Field("is_locked", "Natijalar qulflangan (hakamlar o'zgartira olmaydi)", "bool", default=False),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Nomi", lambda o: o.name),
             ("Usul", lambda o: dict(MODES)[o.scoring_mode].split(" (")[0]),
             ("Maks. ball", lambda o: f"{o.max_score:g}"),
             ("Sinflar", lambda o: ", ".join(g.name for g in o.grades) or "barchasi"),
             ("Qulf", lambda o: "🔒" if o.is_locked else "")],
    validate=subject_validate, after_save=subject_after_save, before_delete=subject_delete,
    extra_template="crud/_subject_groups.html"))

_register(Resource(
    key="blocks", title="Bloklar", singular="blok", model=Block,
    help="Blok — bir nechta fan natijasi birlashtirilib, umumiy reyting tuziladi "
         "(masalan: Matematika + Fizika + Informatika).",
    fields=[Field("name", "Nomi", required=True, placeholder="Aniq fanlar bloki"),
            Field("name_cyr", "Nomi (kirill)", help=_cyr_help()),
            Field("subjects", "Fanlar", "multiselect", options=_subject_options, rel_model=Subject, required=True),
            Field("aggregation", "Hisoblash usuli", "select", options=[
                ("sum", "Ballar yig'indisi"), ("avg_percent", "Foizlar o'rtachasi")], default="sum",
                help="Fanlar maksimal bali har xil bo'lsa «foizlar o'rtachasi» adolatliroq."),
            Field("require_all", "Faqat blokdagi barcha fanlarni tanlaganlar reytingga kirsin", "bool",
                  default=True, help="O'chirilsa, tanlanmagan fan 0 ball hisoblanadi."),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Nomi", lambda o: o.name), ("Fanlar", lambda o: ", ".join(s.name for s in o.subjects)),
             ("Usul", lambda o: "Yig'indi" if o.aggregation == "sum" else "O'rtacha foiz")],
    validate=block_validate))

_register(Resource(
    key="rounds", title="Turlar", singular="tur", model=Round,
    help="Hakamlar «joriy» turga ball kiritadi. Keyingi turga o'tkazish — Reyting sahifasida.",
    fields=[Field("name", "Nomi", required=True, placeholder="1-tur"),
            Field("name_cyr", "Nomi (kirill)", help=_cyr_help()),
            Field("open_to_all", "Barcha ishtirokchilar qatnashadi", "bool", default=True,
                  help="O'chirilsa — faqat shu turga o'tkazilgan ishtirokchilar."),
            Field("is_current", "Joriy tur", "bool", default=False),
            Field("is_locked", "Qulflangan (ball kiritib bo'lmaydi)", "bool", default=False),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Nomi", lambda o: o.name), ("Joriy", lambda o: "⭐ joriy" if o.is_current else ""),
             ("Kimlar", lambda o: "Hammasi" if o.open_to_all else "O'tkazilganlar"),
             ("Qulf", lambda o: "🔒" if o.is_locked else "")],
    after_save=round_after_save, before_delete=round_delete))

_register(Resource(
    key="channels", title="Kanallar", singular="kanal", model=Channel,
    help="O'quvchi ro'yxatdan o'tishdan oldin shu kanallarga obuna bo'lishi kerak. "
         "Telegram kanal obunasini tekshirish uchun botni kanalga ADMIN qilib qo'shing. "
         "Instagram obunasini tekshirib bo'lmaydi — faqat havola ko'rsatiladi.",
    fields=[Field("title", "Nomi", required=True, placeholder="Olimpiada kanali"),
            Field("title_cyr", "Nomi (kirill)", help=_cyr_help()),
            Field("kind", "Turi", "select", options=[("telegram", "Telegram"), ("instagram", "Instagram"),
                                                     ("other", "Boshqa havola")], default="telegram"),
            Field("url", "Havola", "url", required=True, placeholder="https://t.me/kanal_nomi"),
            Field("chat_id", "Kanal ID (Telegram uchun)", placeholder="@kanal_nomi yoki -1001234567890",
                  help="Ochiq kanal: @kanal_nomi. Yopiq kanal: -100 bilan boshlanuvchi raqam."),
            Field("verify", "Obunani tekshirish (bot kanalda admin bo'lishi shart)", "bool", default=True),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Nomi", lambda o: o.title), ("Turi", lambda o: o.kind),
             ("Havola", lambda o: o.url), ("Tekshiriladi", lambda o: _yes(o.verify and o.kind == "telegram"))],
    validate=channel_validate,
    row_actions=[("Botni tekshirish", "/admin/channels/{id}/test")]))

_register(Resource(
    key="rules", title="Nizom", singular="nizom bo'limi", model=Rule,
    help="O'quvchi obunadan keyin nizomni o'qib, «Tushundim» tugmasini bosmaguncha keyingi bosqichga o'tmaydi. "
         "Matn va/yoki fayl (PDF) qo'shishingiz mumkin.",
    fields=[Field("title", "Sarlavha", required=True, placeholder="Olimpiada nizomi"),
            Field("title_cyr", "Sarlavha (kirill)", help=_cyr_help("sarlavha")),
            Field("text", "Matn", "textarea", rows=10),
            Field("text_cyr", "Matn (kirill)", "textarea", rows=6, help=_cyr_help("matn")),
            Field("file", "Fayl (PDF, rasm, Word)", "file"),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Sarlavha", lambda o: o.title), ("Matn", lambda o: (o.text or "")[:60] + ("…" if len(o.text or "") > 60 else "")),
             ("Fayl", lambda o: o.file_name or "—")],
    after_save=rule_after_save))

_register(Resource(
    key="questions", title="Qo'shimcha savollar", singular="savol", model=CustomQuestion,
    help="Asosiy savollardan keyin so'raladi. Javoblar ishtirokchi kartasida va Excel eksportda chiqadi.",
    fields=[Field("text", "Savol matni", "textarea", required=True, rows=2,
                  placeholder="Sizni qaysi o'qituvchi tayyorlagan?"),
            Field("text_cyr", "Savol matni (kirill)", "textarea", rows=2, help=_cyr_help("matn")),
            Field("example", "Namuna", placeholder="Karimov Anvar"),
            Field("example_cyr", "Namuna (kirill)", help=_cyr_help("namuna")),
            Field("qtype", "Javob turi", "select", options=[
                ("text", "Matn"), ("number", "Son"), ("choice", "Bitta variant tanlash"),
                ("multichoice", "Bir nechta variant tanlash"), ("phone", "Telefon raqam")], default="text"),
            Field("options", "Variantlar (har qatorda bittadan)", "textarea", rows=4, group="choice"),
            Field("options_cyr", "Variantlar (kirill, xuddi shu tartibda)", "textarea", rows=4, group="choice",
                  help=_cyr_help("variantlar")),
            Field("min_len", "Minimal uzunlik", "int", default=2, group="text"),
            Field("max_len", "Maksimal uzunlik", "int", default=200, group="text"),
            Field("min_value", "Minimal qiymat", "float", group="number"),
            Field("max_value", "Maksimal qiymat", "float", group="number"),
            Field("required", "Majburiy", "bool", default=True),
            Field("is_active", "Faol", "bool", default=True)],
    columns=[("Savol", lambda o: o.text[:70]), ("Turi", lambda o: o.qtype), ("Majburiy", lambda o: _yes(o.required))],
    validate=question_validate, extra_template="crud/_question_js.html"))


def get_resource(key: str) -> Resource:
    res = RESOURCES.get(key)
    if not res:
        raise HTTPException(404, "Bo'lim topilmadi")
    return res


# ---------------------------------------------------------------------------
# Forma qiymatlarini o'qish
# ---------------------------------------------------------------------------
async def _resolve_options(session, f: Field):
    if callable(f.options):
        return await f.options(session)
    return f.options or []


async def parse_form(session, res: Resource, form) -> tuple[dict, list[str]]:
    data, errs = {}, []
    for f in res.fields:
        if f.type == "file":
            continue
        raw = form.get(f.name)
        if f.type == "bool":
            data[f.name] = raw in ("on", "1", "true")
            continue
        if f.type == "multiselect":
            ids = [int(x) for x in form.getlist(f.name) if str(x).isdigit()]
            objs = (await session.execute(select(f.rel_model).where(f.rel_model.id.in_(ids)))).scalars().all() if ids else []
            data[f.name] = list(objs)
            if f.required and not objs:
                errs.append(f"«{f.label}» tanlanmagan")
            continue
        raw = (raw or "").strip() if isinstance(raw, str) else ""
        if f.type in ("int", "float"):
            if raw == "":
                data[f.name] = f.default  # bo'sh bo'lsa standart qiymat (yoki None)
                continue
            try:
                val = float(raw.replace(",", "."))
                data[f.name] = int(val) if f.type == "int" else val
                if f.type == "int" and val != int(val):
                    errs.append(f"«{f.label}» butun son bo'lishi kerak")
            except ValueError:
                errs.append(f"«{f.label}» son bo'lishi kerak")
            continue
        if f.type == "select":
            opts = [str(v) for v, _ in await _resolve_options(session, f)]
            if raw not in opts:
                if f.required or raw:
                    errs.append(f"«{f.label}» noto'g'ri tanlangan")
                raw = f.default or (opts[0] if opts else "")
            data[f.name] = raw
            continue
        if f.required and not raw:
            errs.append(f"«{f.label}» to'ldirilishi shart")
        data[f.name] = raw
    if res.validate:
        errs += await res.validate(session, data, None)
    return data, errs


def _snapshot(res: Resource, obj) -> dict:
    out = {}
    for f in res.fields:
        if f.type == "file":
            continue
        v = getattr(obj, f.name, None)
        out[f.name] = [x.id for x in v] if f.type == "multiselect" else v
    return out


# ---------------------------------------------------------------------------
# Marshrutlar
# ---------------------------------------------------------------------------
def _order(res: Resource):
    m = res.model
    return [m.position, m.id] if res.has_position else [m.id]


@router.get("/{key}")
async def list_view(key: str, request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    items = (await session.execute(select(res.model).order_by(*_order(res)))).scalars().all()
    usage = {}
    if key == "schools":
        rows = (await session.execute(select(Participant.school_id, func.count())
                                      .group_by(Participant.school_id))).all()
        usage = dict(rows)
    elif key == "grades":
        usage = dict((await session.execute(select(Participant.grade_id, func.count())
                                            .group_by(Participant.grade_id))).all())
    elif key == "subjects":
        usage = dict((await session.execute(select(participant_subjects.c.subject_id, func.count())
                                            .group_by(participant_subjects.c.subject_id))).all())
    elif key == "rounds":
        usage = dict((await session.execute(select(round_participants.c.round_id, func.count())
                                            .group_by(round_participants.c.round_id))).all())
    return render(request, "crud/list.html", {"res": res, "items": items, "usage": usage})


@router.post("/{key}/bulk")
async def bulk_add(key: str, request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    if not res.bulk:
        raise HTTPException(404)
    form = await request.form()
    names = [re.sub(r"\s+", " ", x).strip() for x in str(form.get("names", "")).splitlines()]
    names = [x for x in names if x]
    existing = {o.name.lower() for o in (await session.execute(select(res.model))).scalars().all()}
    pos = (await session.execute(select(func.max(res.model.position)))).scalar() or 0
    added = 0
    for n in names:
        if n.lower() in existing or len(n) > 128:
            continue
        pos += 1
        session.add(res.model(name=n, position=pos))
        existing.add(n.lower())
        added += 1
    audit.log(session, user, "bulk_create", key, "", f"{added} ta qo'shildi", ip=client_ip(request))
    await session.commit()
    flash(request, f"{added} ta {res.singular} qo'shildi" + (f", {len(names) - added} tasi takroriy/uzun edi" if len(names) > added else ""))
    return RedirectResponse(f"/admin/c/{key}", 303)


@router.get("/{key}/new")
async def new_form(key: str, request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    values = {f.name: f.default for f in res.fields}
    for f in res.fields:
        if f.type == "multiselect":
            values[f.name] = []
    return await _form_page(request, session, res, None, values, [])


@router.get("/{key}/{obj_id}")
async def edit_form(key: str, obj_id: int, request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    obj = await session.get(res.model, obj_id)
    if obj is None:
        raise HTTPException(404)
    values = {f.name: getattr(obj, f.name, None) for f in res.fields if f.type != "file"}
    for f in res.fields:
        if f.type == "multiselect":
            values[f.name] = [x.id for x in getattr(obj, f.name)]
    return await _form_page(request, session, res, obj, values, [])


async def _form_page(request, session, res, obj, values, errors, status_code=200):
    options = {f.name: await _resolve_options(session, f) for f in res.fields
               if f.type in ("select", "multiselect")}
    return render(request, "crud/form.html", {"res": res, "obj": obj, "values": values,
                                             "errors": errors, "options": options}, status_code)


@router.post("/{key}/save")
async def save(key: str, request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    form = await request.form()
    obj_id = form.get("id")
    obj = await session.get(res.model, int(obj_id)) if obj_id and str(obj_id).isdigit() else None
    created = obj is None
    data, errs = await parse_form(session, res, form)

    def _form_values():
        v = dict(data)
        for f in res.fields:
            if f.type == "multiselect":
                v[f.name] = [x.id for x in data.get(f.name, [])]
        return v

    if errs:
        return await _form_page(request, session, res, obj, _form_values(), errs, 400)

    old = None if created else _snapshot(res, obj)
    if created:
        obj = res.model()
        if res.has_position:
            obj.position = ((await session.execute(select(func.max(res.model.position)))).scalar() or 0) + 1
    for f in res.fields:
        if f.type == "file":
            continue
        setattr(obj, f.name, data[f.name])
    if created:
        # Qiymatlar (shu jumladan bog'lanishlar) berilgandan keyin sessiyaga qo'shamiz
        session.add(obj)
    if res.after_save:
        err = await res.after_save(session, obj, form, created)
        if err:
            await session.rollback()
            # rollback'dan keyin obyektlar eskiradi — sessiyani tozalab, yangidan yuklaymiz
            session.expunge_all()
            fresh = None if created else await session.get(res.model, int(obj_id))
            return await _form_page(request, session, res, fresh, _form_values(), [err], 400)
    await session.flush()
    audit.log(session, user, "create" if created else "update", key, obj.id,
              f"{res.singular}: {getattr(obj, 'name', None) or getattr(obj, 'title', None) or getattr(obj, 'text', '')[:60]}",
              old=old, new=_snapshot(res, obj), ip=client_ip(request))
    await session.commit()
    ranking.invalidate()
    flash(request, f"{res.singular.capitalize()} saqlandi")
    return RedirectResponse(f"/admin/c/{key}", 303)


@router.post("/{key}/{obj_id}/delete")
async def delete(key: str, obj_id: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    obj = await session.get(res.model, obj_id)
    if obj is None:
        raise HTTPException(404)
    if res.before_delete:
        err = await res.before_delete(session, obj)
        if err:
            flash(request, err, "error")
            return RedirectResponse(f"/admin/c/{key}", 303)
    snap = _snapshot(res, obj)
    if key == "rules" and obj.file_path:
        Path(obj.file_path).unlink(missing_ok=True)
    await session.delete(obj)
    audit.log(session, user, "delete", key, obj_id, f"{res.singular} o'chirildi", old=snap, ip=client_ip(request))
    await session.commit()
    ranking.invalidate()
    flash(request, f"{res.singular.capitalize()} o'chirildi")
    return RedirectResponse(f"/admin/c/{key}", 303)


@router.post("/{key}/{obj_id}/toggle")
async def toggle(key: str, obj_id: int, request: Request, user: User = Depends(require_admin),
                 session: AsyncSession = Depends(get_session)):
    res = get_resource(key)
    obj = await session.get(res.model, obj_id)
    if obj is None:
        raise HTTPException(404)
    obj.is_active = not obj.is_active
    audit.log(session, user, "toggle", key, obj_id, f"faol={obj.is_active}", ip=client_ip(request))
    await session.commit()
    ranking.invalidate()
    return RedirectResponse(f"/admin/c/{key}", 303)


@router.post("/{key}/{obj_id}/move/{direction}")
async def move(key: str, obj_id: int, direction: str, request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    """Tartibni o'zgartirish (yuqoriga/pastga)."""
    res = get_resource(key)
    if not res.has_position:
        raise HTTPException(404)
    items = list((await session.execute(select(res.model).order_by(*_order(res)))).scalars().all())
    idx = next((i for i, o in enumerate(items) if o.id == obj_id), None)
    if idx is None:
        raise HTTPException(404)
    j = idx - 1 if direction == "up" else idx + 1
    if 0 <= j < len(items):
        items[idx], items[j] = items[j], items[idx]
        for pos, o in enumerate(items):
            o.position = pos
        await session.commit()
    return RedirectResponse(f"/admin/c/{key}", 303)


"""Admin: bosh sahifa, sozlamalar, kanalni tekshirish, foydalanuvchilar, jurnal, zaxira."""
from __future__ import annotations

import json
from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import config
from ...db import get_session
from ...models import (AuditLog, Channel, Grade, Participant, Result, School,
                       Subject, User, participant_subjects)
from ...security import hash_password, password_problem
from ...services import audit, backup, ranking
from ...services import settings as st
from ...utils import now_utc, parse_local_input, to_local
from ..core import (Page, client_ip, flash, int_or_none, render,
                    require_admin, verify_csrf)

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_csrf)])


# ---------------------------------------------------------------------------
# Bosh sahifa
# ---------------------------------------------------------------------------
@router.get("")
async def dashboard(request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    total = (await session.execute(select(func.count()).select_from(Participant))).scalar_one()
    active = (await session.execute(select(func.count()).select_from(Participant)
                                    .where(Participant.status == "active"))).scalar_one()
    # Bugungi (mahalliy vaqt bo'yicha) ro'yxatdan o'tganlar
    now_local = to_local(now_utc())
    day_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_utc = day_start.astimezone(timezone.utc).replace(tzinfo=None)
    today = (await session.execute(select(func.count()).select_from(Participant)
                                   .where(Participant.created_at >= day_start_utc))).scalar_one()

    # Oxirgi 14 kun bo'yicha
    since = day_start_utc - timedelta(days=13)
    created = (await session.execute(select(Participant.created_at)
                                     .where(Participant.created_at >= since))).scalars().all()
    days = [(day_start - timedelta(days=i)).strftime("%d.%m") for i in range(13, -1, -1)]
    counts = {d: 0 for d in days}
    for c in created:
        key = to_local(c).strftime("%d.%m")
        if key in counts:
            counts[key] += 1

    by_grade = (await session.execute(
        select(Grade.name, func.count(Participant.id)).join(Participant, Participant.grade_id == Grade.id)
        .group_by(Grade.id).order_by(Grade.position, Grade.id))).all()
    by_school = (await session.execute(
        select(School.name, func.count(Participant.id)).join(Participant, Participant.school_id == School.id)
        .group_by(School.id).order_by(func.count(Participant.id).desc()).limit(12))).all()
    by_subject = (await session.execute(
        select(Subject.id, Subject.name, func.count(participant_subjects.c.participant_id))
        .join(participant_subjects, participant_subjects.c.subject_id == Subject.id)
        .group_by(Subject.id).order_by(Subject.position, Subject.id))).all()

    # Natijalar kiritilishi (joriy tur)
    cur = await ranking.current_round(session)
    progress = []
    if cur:
        entered = dict((await session.execute(
            select(Result.subject_id, func.count()).where(Result.round_id == cur.id)
            .group_by(Result.subject_id))).all())
        for sid, name, _cnt in by_subject:
            subj = await session.get(Subject, sid)
            n = (await session.execute(select(func.count()).select_from(
                ranking.participants_in_round_query(cur, sid).subquery()))).scalar_one()
            progress.append({"subject": subj, "total": n, "entered": entered.get(sid, 0)})
    channels_n = (await session.execute(select(func.count()).select_from(Channel)
                                        .where(Channel.is_active == True))).scalar_one()
    return render(request, "admin/dashboard.html", {
        "total": total, "active": active, "today": today, "reg_state": st.registration_state(),
        "chart": {"days": days, "daily": [counts[d] for d in days],
                  "grades": [list(r) for r in by_grade], "schools": [list(r) for r in by_school],
                  "subjects": [[r[1], r[2]] for r in by_subject]},
        "progress": progress, "round": cur, "channels_n": channels_n,
        "channel_errors": _channel_errors(),
    })


def _channel_errors() -> list[tuple[str, str, str]]:
    """Oxirgi 6 soatda bot tekshira olmagan kanallar: (chat_id, vaqt, xato)."""
    from ...bot.handlers.registration import channel_errors
    from datetime import datetime
    now = datetime.now()
    return [(cid, t.strftime("%d.%m %H:%M"), err) for cid, (t, err) in channel_errors.items()
            if (now - t).total_seconds() < 6 * 3600]


# ---------------------------------------------------------------------------
# Umumiy sozlamalar
# ---------------------------------------------------------------------------
@router.get("/settings")
async def settings_page(request: Request, user: User = Depends(require_admin)):
    return render(request, "admin/settings.html", {"defs": st.DEFAULTS})


@router.post("/settings")
async def settings_save(request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    form = await request.form()
    values, errors = {}, []
    for key, (kind, default, label) in st.DEFAULTS.items():
        if key.startswith("_") or key in st.TEXT_PAGE_KEYS:
            continue  # bu kalitlar «Bot matnlari» sahifasida saqlanadi
        raw = form.get(key)
        if kind == "bool":
            values[key] = raw in ("on", "1")
        elif kind == "int":
            try:
                values[key] = int(raw or 0)
                if values[key] < 0:
                    raise ValueError
            except ValueError:
                errors.append(f"«{label}» musbat butun son bo'lishi kerak")
        elif kind == "datetime":
            try:
                values[key] = parse_local_input(str(raw or ""))
            except ValueError:
                errors.append(f"«{label}» sanasi noto'g'ri")
        else:
            values[key] = str(raw or "").replace("\r\n", "\n").strip()
    if not values.get("olympiad_name"):
        errors.append("Olimpiada nomi bo'sh bo'lmasin")
    s, e = values.get("registration_start"), values.get("registration_end")
    if s and e and s >= e:
        errors.append("Ro'yxatdan o'tish tugash vaqti boshlanishidan keyin bo'lishi kerak")
    if errors:
        for err in errors:
            flash(request, err, "error")
        return RedirectResponse("/admin/settings", 303)
    old = {k: st.get(k) for k in values}
    await st.set_many(session, values)
    changed = {k: v for k, v in values.items() if old.get(k) != v}
    if changed:
        audit.log(session, user, "update", "settings", "", ", ".join(changed), new=changed, ip=client_ip(request))
        await session.commit()
    ranking.invalidate()
    flash(request, "Sozlamalar saqlandi")
    return RedirectResponse("/admin/settings", 303)


@router.post("/toggle/{key}")
async def toggle_setting(key: str, request: Request, user: User = Depends(require_admin),
                         session: AsyncSession = Depends(get_session)):
    """Tezkor yoqish/o'chirish (natijalarni e'lon qilish, sertifikatlar)."""
    if key not in ("results_published", "certificates_enabled", "judges_see_names"):
        raise HTTPException(404)
    new = not st.get(key)
    await st.set_many(session, {key: new})
    audit.log(session, user, "update", "settings", key, f"{key}={new}", ip=client_ip(request))
    await session.commit()
    flash(request, "Saqlandi")
    return RedirectResponse(request.headers.get("referer") or "/admin", 303)


# ---------------------------------------------------------------------------
# Kanalni tekshirish (bot admin qilinganmi)
# ---------------------------------------------------------------------------
@router.post("/channels/{cid}/test")
async def channel_test(cid: int, request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    from ...bot import loader
    ch = await session.get(Channel, cid)
    if ch is None:
        raise HTTPException(404)
    if ch.kind != "telegram":
        flash(request, "Instagram va boshqa havolalarda obunani tekshirib bo'lmaydi — faqat havola ko'rsatiladi.", "info")
    elif loader.bot is None:
        flash(request, "Bot ishlamayapti (BOT_TOKEN berilmagan).", "error")
    elif not ch.chat_id:
        flash(request, "Kanal ID kiritilmagan.", "error")
    else:
        try:
            chat = await loader.bot.get_chat(ch.chat_id)
            me = await loader.bot.get_me()
            member = await loader.bot.get_chat_member(ch.chat_id, me.id)
            if member.status in ("administrator", "creator"):
                from ...bot.handlers.registration import channel_errors
                channel_errors.pop(ch.chat_id.strip(), None)  # ogohlantirishni olib tashlaymiz
                flash(request, f"✅ «{chat.title}» — bot kanalda admin. Obuna tekshiruvi ishlaydi.")
            else:
                flash(request, f"⚠️ «{chat.title}» topildi, lekin bot admin emas (holat: {member.status}). "
                               f"Botni kanalga admin qilib qo'shing.", "warn")
        except Exception as e:
            flash(request, f"❌ Kanal topilmadi yoki bot unga qo'shilmagan: {e}", "error")
    return RedirectResponse("/admin/c/channels", 303)


# ---------------------------------------------------------------------------
# Foydalanuvchilar (adminlar va hakamlar)
# ---------------------------------------------------------------------------
@router.get("/users")
async def users_list(request: Request, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    users = (await session.execute(select(User).order_by(User.role, User.username))).scalars().all()
    counts = dict((await session.execute(select(Result.entered_by_id, func.count())
                                         .group_by(Result.entered_by_id))).all())
    return render(request, "admin/users.html", {"users": users, "counts": counts})


@router.get("/users/new")
async def user_new(request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    return await user_form(request, 0, user, session)


@router.get("/users/{uid}")
async def user_form(request: Request, uid: int, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    obj = await session.get(User, uid) if uid else None
    if uid and obj is None:
        raise HTTPException(404)
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    return render(request, "admin/user_form.html", {"obj": obj, "subjects": subjects, "errors": []})


@router.post("/users/save")
async def user_save(request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    form = await request.form()
    uid = int_or_none(form.get("id"))
    obj = await session.get(User, uid) if uid else None
    username = str(form.get("username", "")).strip().lower()
    password = str(form.get("password", ""))
    role = "admin" if form.get("role") == "admin" else "judge"
    errors = []
    if not username or not all(c.isalnum() or c in "._-" for c in username) or len(username) > 64:
        errors.append("Login faqat lotin harflari, raqamlar va . _ - dan iborat bo'lsin")
    dup = (await session.execute(select(User).where(User.username == username))).scalars().first()
    if dup and (obj is None or dup.id != obj.id):
        errors.append("Bu login band")
    if obj is None or password:
        p = password_problem(password)
        if p:
            errors.append(p)
    if obj is not None and obj.id == user.id and (role != "admin" or not form.get("is_active")):
        errors.append("O'zingizni admin rolidan chiqara yoki bloklay olmaysiz")
    if errors:
        subjects = (await session.execute(select(Subject).order_by(Subject.position))).scalars().all()
        return render(request, "admin/user_form.html", {"obj": obj, "subjects": subjects, "errors": errors,
                                                        "form": form}, 400)
    ids = [int(x) for x in form.getlist("subjects") if str(x).isdigit()]
    chosen = list((await session.execute(select(Subject).where(Subject.id.in_(ids)))).scalars().all()) if ids else []
    created = obj is None
    if created:
        # Bog'lanishlar sessiyaga qo'shishdan OLDIN beriladi (async rejimda yashirin so'rov bo'lmasligi uchun)
        obj = User(username=username, password_hash=hash_password(password), subjects=chosen)
        session.add(obj)
    else:
        obj.username = username
        if password:
            obj.password_hash = hash_password(password)  # eski sessiyalari bekor bo'ladi
    obj.role = role
    obj.full_name = str(form.get("full_name", "")).strip()[:128]
    obj.is_active = bool(form.get("is_active"))
    if not created:
        obj.subjects = chosen
    await session.flush()
    audit.log(session, user, "create" if created else "update", "user", obj.id,
              f"{obj.username} ({obj.role})" + (", parol yangilandi" if password and not created else ""),
              ip=client_ip(request))
    await session.commit()
    flash(request, f"«{obj.username}» saqlandi" + (". Login va parolni hakamga bering." if created else ""))
    return RedirectResponse("/admin/users", 303)


@router.post("/users/{uid}/delete")
async def user_delete(uid: int, request: Request, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    obj = await session.get(User, uid)
    if obj is None:
        raise HTTPException(404)
    if obj.id == user.id:
        flash(request, "O'zingizni o'chira olmaysiz", "error")
        return RedirectResponse("/admin/users", 303)
    name = obj.username
    await session.delete(obj)
    audit.log(session, user, "delete", "user", uid, name, ip=client_ip(request))
    await session.commit()
    flash(request, f"«{name}» o'chirildi (u kiritgan natijalar saqlanib qoladi)")
    return RedirectResponse("/admin/users", 303)


# ---------------------------------------------------------------------------
# Jurnal
# ---------------------------------------------------------------------------
ENTITY_NAMES = {"result": "Natija", "participant": "Ishtirokchi", "settings": "Sozlamalar", "user": "Foydalanuvchi",
                "schools": "Maktab", "grades": "Sinf", "subjects": "Fan", "blocks": "Blok", "rounds": "Tur",
                "channels": "Kanal", "rules": "Nizom", "questions": "Savol", "certificate": "Sertifikat",
                "broadcast": "Xabar", "backup": "Zaxira"}


@router.get("/audit")
async def audit_page(request: Request, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    q = select(AuditLog)
    if params.get("username"):
        q = q.where(AuditLog.username == params["username"])
    if params.get("entity"):
        q = q.where(AuditLog.entity == params["entity"])
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    page = Page(total, int_or_none(params.get("page")) or 1, 50, params)
    rows = (await session.execute(q.order_by(AuditLog.id.desc()).offset(page.offset).limit(page.per_page))).scalars().all()
    usernames = (await session.execute(select(AuditLog.username).distinct())).scalars().all()
    entities = (await session.execute(select(AuditLog.entity).distinct())).scalars().all()

    def pretty(raw: str) -> str:
        if not raw:
            return ""
        try:
            return json.dumps(json.loads(raw), ensure_ascii=False, indent=1)[:800]
        except ValueError:
            return raw[:800]
    return render(request, "admin/audit.html", {"rows": rows, "page": page, "params": params,
                                                "usernames": usernames, "entities": entities,
                                                "names": ENTITY_NAMES, "pretty": pretty})


# ---------------------------------------------------------------------------
# Zaxira nusxalar
# ---------------------------------------------------------------------------
@router.get("/backups")
async def backups_page(request: Request, user: User = Depends(require_admin)):
    files = [{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime} for p in backup.list_backups()]
    return render(request, "admin/backups.html", {"files": files, "keep": config.BACKUP_KEEP})


@router.post("/backups/create")
async def backups_create(request: Request, user: User = Depends(require_admin),
                         session: AsyncSession = Depends(get_session)):
    import asyncio
    path = await asyncio.to_thread(backup.make_backup, "manual")
    audit.log(session, user, "create", "backup", "", path.name, ip=client_ip(request))
    await session.commit()
    flash(request, f"Zaxira nusxa yaratildi: {path.name}")
    return RedirectResponse("/admin/backups", 303)


@router.get("/backups/download/{name}")
async def backups_download(name: str, user: User = Depends(require_admin)):
    path = (config.BACKUP_DIR / name).resolve()
    # Faqat zaxira papkasidagi .db fayllar (yo'lni chetlab o'tishdan himoya)
    if path.parent != config.BACKUP_DIR.resolve() or not path.name.endswith(".db") or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.post("/backups/telegram/{name}")
async def backups_telegram(name: str, request: Request, user: User = Depends(require_admin)):
    """Tanlangan zaxira nusxani ulangan admin Telegram akkauntlariga yuborish."""
    from ...services import notify
    path = (config.BACKUP_DIR / name).resolve()
    if path.parent != config.BACKUP_DIR.resolve() or not path.name.endswith(".db") or not path.exists():
        raise HTTPException(404)
    if not notify.admin_chats():
        flash(request, "Telegram akkaunt ulanmagan. «🔔 Telegram xabarnomalar» sahifasida ulang.", "error")
    else:
        n = await notify.send_backup(path, f"💾 Zaxira nusxa: {path.name} (yuborgan: {user.username})")
        flash(request, f"{n} ta akkauntga yuborildi" if n else "Yuborib bo'lmadi — bot ishlayotganini tekshiring",
              "success" if n else "error")
    return RedirectResponse("/admin/backups", 303)

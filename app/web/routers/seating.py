"""Admin: joylashtirish — binolar, xonalar, smenalar, taqsimlash, o'tirish sxemasi,
ruxsatnomalarni yuborish va chop etish uchun ro'yxatlar."""
from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import config
from ...db import get_session, session_scope
from ...models import (Broadcast, Building, ExamSession, Participant, Room, Round,
                       SeatAssignment, User)
from ...security import verify_password
from ...services import audit, export, jobs, ranking, seating
from ...services import broadcast as bsvc
from ...services import certificates as certs
from ...services import design as dz
from ...services import settings as st
from ...services.pdfwriter import JpegPdfWriter
from ...utils import fmt_dt, now_utc
from ..core import client_ip, flash, int_or_none, render, require_admin, verify_csrf

router = APIRouter(prefix="/admin/seating", dependencies=[Depends(verify_csrf)])
TABS = {"rooms": "🏫 Xonalar", "sessions": "🕘 Smenalar", "plan": "🪑 Taqsimot", "pass": "🎫 Ruxsatnoma",
        "print": "🖨 Chop etish"}
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def _back(tab: str, round_id: Optional[int] = None, extra: str = "") -> RedirectResponse:
    q = f"?tab={tab}" + (f"&round_id={round_id}" if round_id else "") + extra
    return RedirectResponse(f"/admin/seating{q}", 303)


def _time(v, default: str) -> str:
    s = str(v or "").strip()
    m = _TIME.match(s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else default


def _intv(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(v).strip())))
    except (TypeError, ValueError):
        return default


async def _round(session: AsyncSession, round_id: Optional[int]) -> Round:
    if round_id:
        r = await session.get(Round, round_id)
        if r:
            return r
    cur = await ranking.current_round(session)
    if cur is None:
        raise HTTPException(400, "Tur yaratilmagan. «🔁 Turlar» bo'limida tur qo'shing.")
    return cur


# ---------------------------------------------------------------------------
# Asosiy sahifa
# ---------------------------------------------------------------------------
@router.get("")
async def page(request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    params = dict(request.query_params)
    tab = params.get("tab") if params.get("tab") in TABS else "rooms"
    round_ = await _round(session, int_or_none(params.get("round_id")))
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    buildings = (await session.execute(select(Building).order_by(Building.position, Building.id))).scalars().all()
    rooms = (await session.execute(select(Room).order_by(Room.position, Room.id))).scalars().all()
    sessions = (await session.execute(select(ExamSession).where(ExamSession.round_id == round_.id)
                                      .order_by(ExamSession.day, ExamSession.start_time, ExamSession.position,
                                                ExamSession.id))).scalars().all()
    stats = await seating.stats(session, round_)
    one = st.get("seating_one_per_desk")
    # Smena × xona bandligi va konfliktlar
    occ = await seating.occupancy(session, round_.id)
    grid = {}
    total_pen = 0
    for (sid, rid), placed in occ.items():
        room = next((r for r in rooms if r.id == rid), None)
        if room is None:
            continue
        pmap = {s: (a.participant_id, a.participant.school_id) for s, a in placed.items()
                if a.participant.status == "active"}
        pen = seating.total_penalty(room, pmap)
        pairs = len(seating.conflict_pairs(room, pmap))
        total_pen += pen
        grid[(sid, rid)] = {"n": len(pmap), "pairs": pairs, "pen": pen,
                            "schools": len({v[1] for v in pmap.values()}),
                            "max_school": max(Counter(v[1] for v in pmap.values()).values(), default=0)}
    per_session = Counter()
    for (sid, _), g in grid.items():
        per_session[sid] += g["n"]
    # Har smenada qaysi maktablar (maktablar tartibida ko'rsatiladi)
    ranks = await seating.school_ranks(session)
    sess_sch: dict[int, Counter] = defaultdict(Counter)
    names: dict[int, str] = {}
    for (sid, _), placed in occ.items():
        for a in placed.values():
            if a.participant.status == "active":
                sess_sch[sid][a.participant.school_id] += 1
                names[a.participant.school_id] = a.participant.school.name if a.participant.school else "—"
    session_schools = {sid: [(names[k], n) for k, n in sorted(c.items(), key=lambda kv: ranks.get(kv[0], 0))]
                       for sid, c in sess_sch.items()}
    passes = (await session.execute(select(Broadcast).where(Broadcast.kind == "passes")
                                    .order_by(Broadcast.id.desc()).limit(10))).scalars().all()
    pass_tpl = await seating.pass_template(session)
    days = sorted({s.day for s in sessions})
    return render(request, "admin/seating.html", {
        "tab": tab, "tabs": TABS, "round": round_, "rounds": rounds, "buildings": buildings, "rooms": rooms,
        "sessions": sessions, "stats": stats, "one": one, "grid": grid, "per_session": per_session,
        "caps": {r.id: len(seating.room_seats(r, one)) for r in rooms}, "total_pen": total_pen,
        "bcap": {b.id: sum(len(seating.room_seats(r, one)) for r in rooms if r.building_id == b.id and r.is_active)
                 for b in buildings},
        "passes": passes, "pass_tpl": pass_tpl, "days": days, "send_at": {d: seating.pass_send_at(d) for d in days},
        "loads": json.loads, "today": date.today(), "session_schools": session_schools,
    })


# ---------------------------------------------------------------------------
# Binolar
# ---------------------------------------------------------------------------
@router.post("/building")
async def building_save(request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    form = await request.form()
    bid = int_or_none(form.get("id"))
    name = str(form.get("name") or "").strip()[:128]
    if not name:
        flash(request, "Bino nomini yozing", "error")
        return _back("rooms")
    b = await session.get(Building, bid) if bid else None
    if b is None:
        pos = ((await session.execute(select(func.max(Building.position)))).scalar() or 0) + 1
        b = Building(name=name, position=pos)
        session.add(b)
    b.name = name
    b.name_cyr = str(form.get("name_cyr") or "").strip()[:128]
    b.address = str(form.get("address") or "").strip()[:256]
    b.address_cyr = str(form.get("address_cyr") or "").strip()[:256]
    url = str(form.get("map_url") or "").strip()[:512]
    b.map_url = url if url.startswith(("http://", "https://")) else ""
    await session.flush()
    audit.log(session, user, "update" if bid else "create", "building", b.id, b.name, ip=client_ip(request))
    await session.commit()
    flash(request, "Bino saqlandi")
    return _back("rooms")


@router.post("/building/{bid}/delete")
async def building_delete(bid: int, request: Request, user: User = Depends(require_admin),
                          session: AsyncSession = Depends(get_session)):
    b = await session.get(Building, bid)
    if b is None:
        raise HTTPException(404)
    n = (await session.execute(select(func.count()).select_from(Room).where(Room.building_id == bid))).scalar_one()
    if n:
        flash(request, f"Bu binoda {n} ta xona bor — avval xonalarni o'chiring yoki boshqa binoga o'tkazing.", "error")
        return _back("rooms")
    await session.delete(b)
    audit.log(session, user, "delete", "building", bid, b.name, ip=client_ip(request))
    await session.commit()
    flash(request, "Bino o'chirildi")
    return _back("rooms")


# ---------------------------------------------------------------------------
# Xonalar
# ---------------------------------------------------------------------------
def _parse_names(raw: str) -> list[str]:
    """«101-110, 201-205, Akt zal» → ['101', ..., '110', '201', ..., 'Akt zal']."""
    out = []
    for part in re.split(r"[,\n;]+", raw or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m and int(m.group(2)) >= int(m.group(1)) and int(m.group(2)) - int(m.group(1)) < 200:
            out += [str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1)]
        else:
            out.append(part[:64])
    return out


@router.post("/rooms/bulk")
async def rooms_bulk(request: Request, user: User = Depends(require_admin),
                     session: AsyncSession = Depends(get_session)):
    form = await request.form()
    bid = int_or_none(form.get("building_id"))
    if not bid or await session.get(Building, bid) is None:
        flash(request, "Avval bino qo'shing va tanlang", "error")
        return _back("rooms")
    names = _parse_names(str(form.get("names") or ""))
    if not names:
        flash(request, "Xona nomlarini yozing: masalan «101-110, 201-205»", "error")
        return _back("rooms")
    rows, desks = _intv(form.get("rows"), 5, 1, 30), _intv(form.get("desks"), 3, 1, 12)
    spd = _intv(form.get("seats_per_desk"), 2, 1, 4)
    floor_mode = str(form.get("floor") or "").strip()
    existing = {r.name for r in (await session.execute(select(Room).where(Room.building_id == bid))).scalars()}
    pos = (await session.execute(select(func.max(Room.position)))).scalar() or 0
    added = 0
    for name in names:
        if name in existing:
            continue
        floor = floor_mode if floor_mode and floor_mode != "auto" else (
            name[0] if floor_mode == "auto" and name.isdigit() and len(name) >= 3 else "")
        pos += 1
        session.add(Room(building_id=bid, name=name, floor=floor[:16], rows=rows, desks=desks,
                         seats_per_desk=spd, position=pos))
        added += 1
    audit.log(session, user, "create", "room", "", f"{added} ta xona qo'shildi ({rows}×{desks}×{spd})", ip=client_ip(request))
    await session.commit()
    flash(request, f"{added} ta xona qo'shildi" + (f" ({len(names) - added} tasi avvaldan bor edi)" if added < len(names) else ""))
    return _back("rooms")


async def _room_occupied(session: AsyncSession, room_id: int) -> set[str]:
    rows = (await session.execute(select(SeatAssignment.row, SeatAssignment.desk, SeatAssignment.seat)
                                  .where(SeatAssignment.room_id == room_id))).all()
    return {f"{r}-{d}-{s}" for r, d, s in rows}


@router.post("/room/{rid}")
async def room_save(rid: int, request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    room = await session.get(Room, rid)
    if room is None:
        raise HTTPException(404)
    form = await request.form()
    name = str(form.get("name") or "").strip()[:64]
    if not name:
        flash(request, "Xona nomi bo'sh", "error")
        return RedirectResponse(f"/admin/seating/room/{rid}", 303)
    rows, desks = _intv(form.get("rows"), room.rows, 1, 30), _intv(form.get("desks"), room.desks, 1, 12)
    spd = _intv(form.get("seats_per_desk"), room.seats_per_desk, 1, 4)
    taken = await _room_occupied(session, rid)
    lost = [k for k in taken if not (int(k.split("-")[0]) <= rows and int(k.split("-")[1]) <= desks
                                     and int(k.split("-")[2]) <= spd)]
    if lost:
        flash(request, f"{len(lost)} ta band o'rin yo'qolib qoladi. Avval o'sha o'quvchilarni ko'chiring yoki taqsimotni qayta bajaring.", "error")
        return RedirectResponse(f"/admin/seating/room/{rid}", 303)
    room.name, room.floor = name, str(form.get("floor") or "").strip()[:16]
    room.rows, room.desks, room.seats_per_desk = rows, desks, spd
    bid = int_or_none(form.get("building_id"))
    if bid and await session.get(Building, bid):
        room.building_id = bid
    room.is_active = bool(form.get("is_active"))
    # O'chirilgan o'rinlar yangi o'lchamdan tashqarida qolsa — tozalaymiz
    room.disabled_json = json.dumps(sorted(k for k in seating.disabled_set(room)
                                           if int(k.split("-")[0]) <= rows and int(k.split("-")[1]) <= desks
                                           and int(k.split("-")[2]) <= spd))
    audit.log(session, user, "update", "room", rid, f"{room.name}: {rows}×{desks}×{spd}", ip=client_ip(request))
    await session.commit()
    flash(request, "Xona saqlandi")
    return RedirectResponse(f"/admin/seating/room/{rid}", 303)


@router.post("/room/{rid}/delete")
async def room_delete(rid: int, request: Request, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    room = await session.get(Room, rid)
    if room is None:
        raise HTTPException(404)
    n = (await session.execute(select(func.count()).select_from(SeatAssignment)
                               .where(SeatAssignment.room_id == rid))).scalar_one()
    if n:
        flash(request, f"Bu xonada {n} ta o'quvchi joylangan. Avval taqsimotni bekor qiling yoki xonani nofaol qiling.", "error")
        return RedirectResponse(f"/admin/seating/room/{rid}", 303)
    await session.delete(room)
    audit.log(session, user, "delete", "room", rid, room.name, ip=client_ip(request))
    await session.commit()
    flash(request, "Xona o'chirildi")
    return _back("rooms")


@router.post("/room/{rid}/toggle-seat")
async def room_toggle_seat(rid: int, request: Request, user: User = Depends(require_admin),
                           session: AsyncSession = Depends(get_session)):
    """Singan/ishlatilmaydigan o'rinni o'chirish yoki qayta yoqish."""
    room = await session.get(Room, rid)
    if room is None:
        raise HTTPException(404)
    form = await request.form()
    key = str(form.get("seat") or "")
    if not re.match(r"^\d+-\d+-\d+$", key):
        raise HTTPException(400, "O'rin noto'g'ri")
    off = seating.disabled_set(room)
    if key in off:
        off.discard(key)
    else:
        if key in await _room_occupied(session, rid):
            flash(request, "Bu o'rinda o'quvchi bor — avval uni ko'chiring.", "error")
            return RedirectResponse(f"/admin/seating/room/{rid}?{request.url.query}", 303)
        off.add(key)
    room.disabled_json = json.dumps(sorted(off))
    await session.commit()
    return RedirectResponse(f"/admin/seating/room/{rid}?{form.get('back') or ''}", 303)


@router.get("/room/{rid}")
async def room_page(rid: int, request: Request, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    """O'tirish sxemasi: maktablar rang bilan, bir maktabdan qo'shni o'tirganlar qizil chiziq bilan."""
    room = await session.get(Room, rid)
    if room is None:
        raise HTTPException(404)
    params = dict(request.query_params)
    round_ = await _round(session, int_or_none(params.get("round_id")))
    sessions = await seating.round_sessions(session, round_.id)
    sess = next((s for s in sessions if str(s.id) == params.get("session_id")), sessions[0] if sessions else None)
    placed = {}
    if sess:
        rows = (await session.execute(select(SeatAssignment).where(
            SeatAssignment.round_id == round_.id, SeatAssignment.session_id == sess.id,
            SeatAssignment.room_id == rid))).scalars().all()
        placed = {(a.row, a.desk, a.seat): a for a in rows}
    pmap = {s: (a.participant_id, a.participant.school_id) for s, a in placed.items()}
    conflicts = seating.conflict_pairs(room, pmap)
    bad = {s for c in conflicts for s in c[:2]}
    schools = Counter(a.participant.school_id for a in placed.values())
    palette = ["#fde68a", "#bfdbfe", "#bbf7d0", "#fbcfe8", "#ddd6fe", "#fed7aa", "#a5f3fc", "#e9d5ff", "#fecaca",
               "#d9f99d", "#c7d2fe", "#fef08a", "#99f6e4", "#f5d0fe", "#e7e5e4"]
    colors = {sid: palette[i % len(palette)] for i, (sid, _) in enumerate(schools.most_common())}
    names = {a.participant.school_id: a.participant.school.name for a in placed.values() if a.participant.school}
    rooms = (await session.execute(select(Room).where(Room.is_active == True).order_by(Room.position, Room.id))).scalars().all()  # noqa: E712
    buildings = (await session.execute(select(Building).order_by(Building.position, Building.id))).scalars().all()
    idx = [r.id for r in rooms]
    pos = idx.index(rid) if rid in idx else -1
    return render(request, "admin/seating_room.html", {
        "room": room, "round": round_, "sessions": sessions, "sess": sess, "placed": placed, "bad": bad,
        "conflicts": conflicts, "colors": colors, "school_names": names, "schools": schools,
        "disabled": seating.disabled_set(room), "rooms": rooms, "buildings": buildings,
        "prev_room": rooms[pos - 1] if pos > 0 else None, "next_room": rooms[pos + 1] if 0 <= pos < len(rooms) - 1 else None,
        "seat_number": seating.seat_number, "one": st.get("seating_one_per_desk"),
        "capacity": len(seating.room_seats(room, st.get("seating_one_per_desk"))),
    })


@router.post("/move")
async def move(request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    """O'quvchini ko'chirish (bo'sh o'ringa yoki boshqa o'quvchi bilan almashtirish)."""
    form = await request.form()
    a = await session.get(SeatAssignment, int_or_none(form.get("assignment_id")) or 0)
    back = str(form.get("back") or "/admin/seating?tab=plan")
    if not back.startswith("/admin/seating"):
        back = "/admin/seating?tab=plan"
    if a is None:
        flash(request, "O'quvchi topilmadi", "error")
        return RedirectResponse(back, 303)
    sid, rid = int_or_none(form.get("session_id")) or a.session_id, int_or_none(form.get("room_id")) or a.room_id
    seat = None
    key = str(form.get("seat") or "")
    if re.match(r"^\d+-\d+-\d+$", key):
        seat = tuple(int(x) for x in key.split("-"))
    p = a.participant
    old_sent, before = a.pass_sent_at, (a.session_id, a.room_id, a.row, a.desk, a.seat)
    try:
        other = await seating.move(session, a, sid, rid, seat)  # type: ignore[arg-type]
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse(back, 303)
    changed = [(p, old_sent)]
    if other is not None:
        changed.append((other.participant, other.pass_sent_at))
    audit.log(session, user, "update", "seat", a.id,
              f"{p.full_name} ko'chirildi: {before} → {(a.session_id, a.room_id, a.row, a.desk, a.seat)}"
              + (f"; almashildi: {other.participant.full_name}" if other else ""), ip=client_ip(request))
    await session.commit()
    # Ruxsatnomasi oldin yuborilgan bo'lsa — yangisini darhol yuboramiz
    from ...bot import loader
    resent = 0
    if loader.bot is not None:
        for person, sent_at in changed:
            if sent_at is not None:
                if await seating.send_pass(loader.bot, session, person, changed=True) == "ok":
                    resent += 1
        await session.commit()
    msg = f"{p.full_name} ko'chirildi" + (f" (o'rni {other.participant.full_name} bilan almashtirildi)" if other else "")
    if resent:
        msg += f". Yangi ruxsatnoma botga yuborildi: {resent} ta"
    flash(request, msg)
    return RedirectResponse(back, 303)


# ---------------------------------------------------------------------------
# Smenalar
# ---------------------------------------------------------------------------
def _parse_day(v) -> Optional[date]:
    try:
        return datetime.strptime(str(v or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


@router.post("/session")
async def session_save(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    sid = int_or_none(form.get("id"))
    day = _parse_day(form.get("day"))
    name = str(form.get("name") or "").strip()[:64]
    if day is None or not name:
        flash(request, "Smena nomi va sanasini kiriting", "error")
        return _back("sessions", round_.id)
    s = await session.get(ExamSession, sid) if sid else None
    if s is None:
        pos = ((await session.execute(select(func.max(ExamSession.position)))).scalar() or 0) + 1
        s = ExamSession(round_id=round_.id, name=name, day=day, position=pos)
        session.add(s)
    s.name, s.name_cyr, s.day = name, str(form.get("name_cyr") or "").strip()[:64], day
    s.arrive_time = _time(form.get("arrive_time"), "08:30")
    s.start_time = _time(form.get("start_time"), "09:00")
    s.duration_min = _intv(form.get("duration_min"), 120, 10, 600)
    s.is_active = bool(form.get("is_active", "1"))
    await session.flush()
    audit.log(session, user, "update" if sid else "create", "exam_session", s.id,
              f"{s.name}: {s.day} {s.start_time}", ip=client_ip(request))
    await session.commit()
    flash(request, "Smena saqlandi")
    return _back("sessions", round_.id)


@router.post("/session/{sid}/delete")
async def session_delete(sid: int, request: Request, user: User = Depends(require_admin),
                         session: AsyncSession = Depends(get_session)):
    s = await session.get(ExamSession, sid)
    if s is None:
        raise HTTPException(404)
    n = (await session.execute(select(func.count()).select_from(SeatAssignment)
                               .where(SeatAssignment.session_id == sid))).scalar_one()
    if n:
        flash(request, f"Bu smenada {n} ta o'quvchi joylangan. Avval taqsimotni bekor qiling.", "error")
        return _back("sessions", s.round_id)
    await session.delete(s)
    audit.log(session, user, "delete", "exam_session", sid, s.name, ip=client_ip(request))
    await session.commit()
    flash(request, "Smena o'chirildi")
    return _back("sessions", s.round_id)


@router.post("/sessions/generate")
async def sessions_generate(request: Request, user: User = Depends(require_admin),
                            session: AsyncSession = Depends(get_session)):
    """Kerakli miqdorda smenani avtomatik yaratish: boshlanish sanasi, kuniga vaqtlar ro'yxati."""
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    day = _parse_day(form.get("start_day"))
    times = [_time(t, "") for t in re.split(r"[,\s;]+", str(form.get("times") or "")) if t.strip()]
    times = [t for t in times if t]
    count = _intv(form.get("count"), 1, 1, 40)
    before = _intv(form.get("arrive_before"), 30, 0, 180)
    dur = _intv(form.get("duration_min"), 120, 10, 600)
    skip_sunday = bool(form.get("skip_sunday"))
    if day is None or not times:
        flash(request, "Boshlanish sanasi va kamida bitta vaqt kiriting (masalan: 09:00, 13:00)", "error")
        return _back("sessions", round_.id)
    existing = (await session.execute(select(func.count()).select_from(ExamSession)
                                      .where(ExamSession.round_id == round_.id))).scalar_one()
    pos = (await session.execute(select(func.max(ExamSession.position)))).scalar() or 0
    made, d = 0, day
    while made < count:
        if skip_sunday and d.weekday() == 6:
            d += timedelta(days=1)
            continue
        for t in sorted(times):
            if made >= count:
                break
            h, m = map(int, t.split(":"))
            arrive = (datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=m - before)).strftime("%H:%M")
            pos += 1
            made += 1
            session.add(ExamSession(round_id=round_.id, name=f"{existing + made}-smena", day=d, start_time=t,
                                    arrive_time=arrive, duration_min=dur, position=pos))
        d += timedelta(days=1)
    audit.log(session, user, "create", "exam_session", "", f"{made} ta smena yaratildi", ip=client_ip(request))
    await session.commit()
    flash(request, f"{made} ta smena yaratildi")
    return _back("sessions", round_.id)


# ---------------------------------------------------------------------------
# Taqsimlash
# ---------------------------------------------------------------------------
@router.post("/options")
async def options(request: Request, user: User = Depends(require_admin),
                  session: AsyncSession = Depends(get_session)):
    form = await request.form()
    group = form.get("group_by") if form.get("group_by") in ("none", "grade", "subject", "school_order") else "none"
    await st.set_many(session, {"seating_one_per_desk": bool(form.get("one_per_desk")), "seating_group_by": group})
    flash(request, "Sozlamalar saqlandi")
    return _back("plan", int_or_none(form.get("round_id")))


@router.post("/run")
async def run(request: Request, user: User = Depends(require_admin),
              session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    rid = round_.id  # rollback'dan keyin obyekt eskiradi — raqamni oldindan olamiz
    try:
        res = await seating.run_full(session, round_, seed=secrets.randbelow(10 ** 9))
    except ValueError as e:
        await session.rollback()
        flash(request, str(e), "error")
        return _back("plan", rid)
    rooms_bad = sum(1 for v in res.room_penalty.values() if v)
    audit.log(session, user, "create", "seating", round_.id,
              f"Taqsimlandi: {len(res.assignments)} o'quvchi, jarima {res.penalty}", ip=client_ip(request))
    await session.commit()
    flash(request, f"✅ {len(res.assignments)} ta o'quvchi joylandi. " +
          ("Bir maktabdan qo'shni o'tirganlar yo'q 👍" if res.penalty == 0 else
           f"{rooms_bad} ta xonada bir maktabdan yaqin o'tirganlar bor (maktab juda katta bo'lsa — buni butunlay oldini olib bo'lmaydi). "
           "Sxemada qizil bilan ko'rsatilgan."))
    return _back("plan", round_.id)


@router.post("/fill")
async def fill(request: Request, user: User = Depends(require_admin),
               session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    placed, freed, errors = await seating.place_missing(session, round_)
    audit.log(session, user, "create", "seating", round_.id, f"Qo'shimcha joylash: {placed}, bo'shatildi: {freed}",
              ip=client_ip(request))
    await session.commit()
    msg = f"{placed} ta yangi o'quvchi bo'sh joylarga qo'yildi" + (f", {freed} ta o'rin bo'shatildi" if freed else "")
    flash(request, msg if placed or freed else "Joylanmagan o'quvchi yo'q")
    if errors:
        flash(request, f"{len(errors)} ta o'quvchiga joy yetmadi: " + "; ".join(errors[:5]), "error")
    return _back("plan", round_.id)


@router.post("/reset")
async def reset(request: Request, user: User = Depends(require_admin),
                session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    if not verify_password(str(form.get("password") or ""), user.password_hash):
        flash(request, "Parol noto'g'ri", "error")
        return _back("plan", round_.id)
    n = (await session.execute(delete(SeatAssignment).where(SeatAssignment.round_id == round_.id))).rowcount
    audit.log(session, user, "delete", "seating", round_.id, f"Taqsimot bekor qilindi: {n} ta", ip=client_ip(request))
    await session.commit()
    flash(request, f"Taqsimot bekor qilindi ({n} ta o'rin bo'shatildi)")
    return _back("plan", round_.id)


# ---------------------------------------------------------------------------
# Ruxsatnoma
# ---------------------------------------------------------------------------
@router.post("/pass-settings")
async def pass_settings(request: Request, user: User = Depends(require_admin),
                        session: AsyncSession = Depends(get_session)):
    form = await request.form()
    await st.set_many(session, {"pass_send_time": _time(form.get("pass_send_time"), "18:00"),
                                "pass_notice": bool(form.get("pass_notice")),
                                "passes_published": bool(form.get("passes_published"))})
    flash(request, "Ruxsatnoma sozlamalari saqlandi")
    return _back("pass", int_or_none(form.get("round_id")))


def _pass_filters(round_id: int, day: Optional[date] = None, only_unsent: bool = True) -> str:
    f = {"seat_round": str(round_id)}
    if day:
        f["exam_day"] = day.isoformat()
    if only_unsent:
        f["pass_unsent"] = "1"
    return json.dumps(f)


@router.post("/schedule")
async def schedule(request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    """Har bir olimpiada kuni uchun: bir kun oldin belgilangan soatda avtomatik yuborish."""
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    if await seating.pass_template(session) is None:
        flash(request, "Faol ruxsatnoma shabloni yo'q («🎓 Sertifikatlar» → Ruxsatnoma)", "error")
        return _back("pass", round_.id)
    sessions = await seating.round_sessions(session, round_.id)
    days = sorted({s.day for s in sessions})
    # Avvalgi rejalashtirilganlarini bekor qilamiz (qayta bosilsa takrorlanmasin)
    for b in (await session.execute(select(Broadcast).where(Broadcast.kind == "passes",
                                                            Broadcast.status == "scheduled",
                                                            Broadcast.round_id == round_.id))).scalars():
        b.status = "cancelled"
    made, late = [], []
    for d in days:
        at = seating.pass_send_at(d)
        if at < now_utc():
            late.append(d)
            at = now_utc()
        session.add(Broadcast(kind="passes", round_id=round_.id, filters_json=_pass_filters(round_.id, d),
                              scheduled_at=at, created_by=user.username, text=f"Ruxsatnoma: {d.isoformat()}"))
        made.append(f"{d:%d.%m} → {fmt_dt(at)}")
    audit.log(session, user, "create", "broadcast", "", "Ruxsatnomalar rejalashtirildi: " + ", ".join(made),
              ip=client_ip(request))
    await session.commit()
    bsvc.wake()
    flash(request, "📅 Rejalashtirildi: " + "; ".join(made) if made else "Smenalar yo'q", "success" if made else "error")
    if late:
        flash(request, "Vaqti o'tib ketgan kun(lar) uchun hozir yuboriladi: " + ", ".join(f"{d:%d.%m}" for d in late), "info")
    return _back("pass", round_.id)


@router.post("/send-now")
async def send_now(request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    form = await request.form()
    round_ = await _round(session, int_or_none(form.get("round_id")))
    if await seating.pass_template(session) is None:
        flash(request, "Faol ruxsatnoma shabloni yo'q", "error")
        return _back("pass", round_.id)
    only_unsent = form.get("mode") != "all"
    b = Broadcast(kind="passes", round_id=round_.id, filters_json=_pass_filters(round_.id, None, only_unsent),
                  scheduled_at=now_utc(), created_by=user.username,
                  text="Ruxsatnoma: " + ("yuborilmaganlarga" if only_unsent else "hammaga (qayta)"))
    session.add(b)
    await session.flush()
    n = await bsvc.count_recipients(b)
    audit.log(session, user, "create", "broadcast", b.id, f"Ruxsatnomalar hozir: {n} ta", ip=client_ip(request))
    await session.commit()
    bsvc.wake()
    flash(request, f"Yuborish boshlandi: {n} ta o'quvchi. Holatini shu sahifada ko'rasiz.")
    return _back("pass", round_.id)


@router.post("/send-one/{pid}")
async def send_one(pid: int, request: Request, user: User = Depends(require_admin),
                   session: AsyncSession = Depends(get_session)):
    from ...bot import loader
    p = await session.get(Participant, pid)
    if p is None:
        raise HTTPException(404)
    if loader.bot is None:
        flash(request, "Bot ishlamayapti", "error")
    else:
        res = await seating.send_pass(loader.bot, session, p)
        await session.commit()
        flash(request, {"ok": "Ruxsatnoma yuborildi", "blocked": "O'quvchi botni bloklagan",
                        "skip": "O'quvchiga joy biriktirilmagan yoki faol ruxsatnoma shabloni yo'q"}.get(res, "Yuborib bo'lmadi"),
              "success" if res == "ok" else "error")
    return RedirectResponse(f"/admin/participants/{pid}", 303)


@router.get("/pass/{pid}.{ext}")
async def pass_file(pid: int, ext: str, user: User = Depends(require_admin),
                    session: AsyncSession = Depends(get_session)):
    p = await session.get(Participant, pid)
    tpl = await seating.pass_template(session)
    ctx = await seating.pass_context(session, p) if p else None
    if p is None or tpl is None or ctx is None:
        raise HTTPException(404, "Joy biriktirilmagan yoki faol ruxsatnoma shabloni yo'q")
    img = await asyncio.to_thread(certs.render_template, tpl, ctx)
    if ext == "pdf":
        data = await asyncio.to_thread(dz.to_pdf, img, certs.template_dpi(tpl))
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="ruxsatnoma_{p.code}.pdf"'})
    return Response(await asyncio.to_thread(dz.to_png, img, 1200), media_type="image/png")


# ---------------------------------------------------------------------------
# Chop etish va Excel
# ---------------------------------------------------------------------------
async def _rows_for_export(session: AsyncSession, round_: Round) -> list[tuple]:
    rows = (await session.execute(select(SeatAssignment).where(SeatAssignment.round_id == round_.id))).scalars().all()
    out = []
    for a in rows:
        p = a.participant
        if p.status != "active":
            continue
        out.append((a.session, a.room, a, p))
    out.sort(key=lambda x: (x[0].day, x[0].start_time, x[0].id, x[1].position, x[1].id, x[2].row, x[2].desk, x[2].seat))
    return out


@router.get("/export.xlsx")
async def export_xlsx(request: Request, round_id: Optional[int] = None, user: User = Depends(require_admin),
                      session: AsyncSession = Depends(get_session)):
    round_ = await _round(session, round_id)
    data = await _rows_for_export(session, round_)
    headers = ["Sana", "Smena", "Kelish", "Boshlanish", "Bino", "Xona", "O'rin №", "Qator", "Parta", "O'rin",
               "ID", "Familiya", "Ism", "Maktab", "Sinf", "Telefon", "Ruxsatnoma yuborilgan"]
    widths = [11, 10, 8, 10, 22, 10, 8, 7, 7, 7, 8, 18, 16, 26, 7, 16, 18]

    def row(s, r, a, p):
        return [s.day.strftime("%d.%m.%Y"), s.name, s.arrive_time, s.start_time, r.building.name if r.building else "",
                r.name, seating.seat_number(r, (a.row, a.desk, a.seat)), a.row, a.desk, a.seat, p.code,
                p.last_name, p.first_name, p.school.name if p.school else "", p.grade.name if p.grade else "",
                p.phone, fmt_dt(a.pass_sent_at)]
    sheets = [{"title": "Barcha o'rinlar", "headers": headers, "rows": [row(*x) for x in data], "widths": widths}]
    by_school: dict[str, list] = defaultdict(list)
    for x in data:
        by_school[x[3].school.name if x[3].school else "—"].append(row(*x))
    used = set()
    for name in sorted(by_school):
        title = name[:28]
        n = 2
        while title.lower() in used:
            title = f"{name[:25]} {n}"
            n += 1
        used.add(title.lower())
        sheets.append({"title": title, "headers": headers, "rows": by_school[name], "widths": widths})
    content = await asyncio.to_thread(export.workbook_from_sheets, sheets)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="joylashtirish_{datetime.now(config.TZ):%Y%m%d_%H%M}.xlsx"'})


@router.post("/print")
async def print_rooms(request: Request, user: User = Depends(require_admin)):
    """Har xona eshigi uchun ro'yxat va o'tirish sxemasi (PDF, fonda tayyorlanadi)."""
    form = await request.form()
    round_id = int_or_none(form.get("round_id"))
    only_session = int_or_none(form.get("session_id"))
    who, ip = SimpleNamespace(id=user.id, username=user.username), client_ip(request)

    async def worker(job: jobs.Job) -> None:
        from ...services import seating_print
        async with session_scope() as s:
            round_ = await _round(s, round_id)
            data = await _rows_for_export(s, round_)
            groups: dict[tuple, list] = defaultdict(list)
            for sess, room, a, p in data:
                if only_session and sess.id != only_session:
                    continue
                groups[(sess.id, room.id)].append((sess, room, a, p))
            job.total = len(groups)
            if not groups:
                job.error = "Joylangan o'quvchilar yo'q"
                return
            path = jobs.exports_dir() / f"xonalar_{secrets.token_hex(4)}.pdf"
            writer = JpegPdfWriter(path, seating_print.DPI, quality=90)
            try:
                for key in sorted(groups, key=lambda k: (groups[k][0][0].day, groups[k][0][0].start_time, k[0],
                                                         groups[k][0][1].position, k[1])):
                    items = groups[key]
                    pages = await asyncio.to_thread(seating_print.room_pages, items, st.get("olympiad_name"))
                    for img in pages:
                        await asyncio.to_thread(writer.add, img)
                    job.done += 1
            finally:
                writer.close()
            job.path, job.filename, job.media_type = path, "xonalar_royxati.pdf", "application/pdf"
            audit.log(s, who, "export", "seating", round_.id, f"Xona ro'yxatlari PDF: {len(groups)} ta", ip=ip)
            await s.commit()

    job = jobs.start("Xona ro'yxatlari", user.id, worker)
    return HTMLResponse(_job_html(job))


def _job_html(job: jobs.Job) -> str:
    from html import escape
    if job.status == "running":
        return (f'<div class="job" hx-get="/admin/seating/jobs/{job.id}" hx-trigger="every 2s" hx-swap="outerHTML">'
                f'⏳ Tayyorlanmoqda: <b>{job.done}</b> / {job.total or "…"} xona'
                f'<div class="bar"><span style="width:{job.percent}%"></span></div></div>')
    if job.status == "done":
        return (f'<div class="job ok">✅ Tayyor: {job.done} ta xona. '
                f'<a class="btn sm primary" href="/admin/certificates/jobs/{job.id}/download">⬇️ Yuklab olish</a></div>')
    return f'<div class="job err">⚠️ {escape(job.error)}</div>'


@router.get("/jobs/{job_id}")
async def job_status(job_id: str, user: User = Depends(require_admin)):
    job = jobs.get(job_id, user.id)
    if job is None:
        return HTMLResponse('<div class="job err">Ish topilmadi</div>')
    return HTMLResponse(_job_html(job))

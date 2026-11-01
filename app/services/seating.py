"""Joylashtirish: o'quvchilarni smena → xona → o'rindiqqa taqsimlash va ruxsatnomalar.

Algoritm (3 bosqich):
  1) Smenalarga: o'quvchilar smenalarga teng bo'linadi (guruhlash yoqilsa — sinf yoki fan bo'yicha
     ketma-ket, bitta guruh imkon qadar bitta smenada).
  2) Xonalarga: har smenada xonalar sig'imiga mutanosib to'ldiriladi. Eng ko'p ishtirokchisi bor
     maktabdan boshlab har o'quvchi o'z maktabidan ENG KAM odami bor xonaga tushadi
     (maktab xonalar bo'ylab tekis tarqaladi).
  3) O'rindiqlarga: «qo'shni» jarimalari — bir partada 10, yon partada 4, old/orqa 2, diagonal 1.
     Avval maktablar navbatma-navbat «ilon izi» bo'ylab joylanadi, so'ng mahalliy qidiruv bilan
     (konfliktli o'quvchini eng yaxshi joy bilan almashtirish) jarima kamaytiriladi.
     Bir maktab xonaning yarmidan ko'pini egallasa — 0 konflikt matematik jihatdan mumkin emas,
     qolgan konfliktlar soni ko'rsatiladi.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import config
from ..models import (Building, ExamSession, Participant, Room, Round,
                      SeatAssignment)
from ..utils import by_lang, lat2cyr, now_utc
from . import ranking
from . import settings as st

MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust", "sentabr", "oktabr",
          "noyabr", "dekabr"]
WEEKDAYS = ["dushanba", "seshanba", "chorshanba", "payshanba", "juma", "shanba", "yakshanba"]

# Qo'shnilik jarimalari
W_DESK, W_SIDE, W_FRONT, W_DIAG = 10, 4, 2, 1


# ---------------------------------------------------------------------------
# Xona geometriyasi
# ---------------------------------------------------------------------------
Seat = tuple[int, int, int]  # (qator, parta, o'rin)


def disabled_set(room: Room) -> set[str]:
    try:
        return {str(x) for x in json.loads(room.disabled_json or "[]")}
    except ValueError:
        return set()


def seat_key(seat: Seat) -> str:
    return f"{seat[0]}-{seat[1]}-{seat[2]}"


def room_seats(room: Room, one_per_desk: bool = False) -> list[Seat]:
    """Xonadagi ishlatiladigan o'rinlar: oldingi qatordan, chapdan o'ngga."""
    off = disabled_set(room)
    out = []
    for r in range(1, room.rows + 1):
        for d in range(1, room.desks + 1):
            for s in range(1, room.seats_per_desk + 1):
                if one_per_desk and room.seats_per_desk > 1 and s != 1:
                    continue
                if f"{r}-{d}-{s}" not in off:
                    out.append((r, d, s))
    return out


def seat_number(room: Room, seat: Seat) -> int:
    """Xonadagi o'rin raqami (1 dan): qatorlar bo'yicha chapdan o'ngga (o'chirilganlar ham sanaladi)."""
    r, d, s = seat
    return (r - 1) * room.desks * room.seats_per_desk + (d - 1) * room.seats_per_desk + s


def neighbors(room: Room, seat: Seat) -> list[tuple[Seat, int]]:
    """O'rinning qo'shnilari va jarima og'irligi.

    Ustun (x) — partalar ketma-ketligidagi o'rin: x = (parta-1)*S + (o'rin-1).
    """
    S = room.seats_per_desk
    r, d, s = seat
    x = (d - 1) * S + (s - 1)
    out = []
    for dr in (-1, 0, 1):
        rr = r + dr
        if rr < 1 or rr > room.rows:
            continue
        for dx in (-1, 0, 1):
            if dr == 0 and dx == 0:
                continue
            xx = x + dx
            if xx < 0 or xx >= room.desks * S:
                continue
            dd, ss = xx // S + 1, xx % S + 1
            if dr == 0:
                w = W_DESK if dd == d else W_SIDE
            elif dx == 0:
                w = W_FRONT
            else:
                w = W_DIAG
            out.append(((rr, dd, ss), w))
    return out


# ---------------------------------------------------------------------------
# 3-bosqich: xona ichida o'rindiqlar
# ---------------------------------------------------------------------------
def arrange_room(room: Room, students: list[tuple[int, int]], one_per_desk: bool,
                 rng: random.Random, fixed: Optional[dict[Seat, tuple[int, int]]] = None,
                 iterations: int = 0) -> tuple[dict[int, Seat], int]:
    """students: [(participant_id, school_id)]. fixed — o'zgarmaydigan (qulflangan) o'rinlar.

    Natija: ({pid: seat}, jarima).
    """
    fixed = fixed or {}
    seats = [s for s in room_seats(room, one_per_desk) if s not in fixed]
    # Bo'sh joy ko'p bo'lsa — avval har partaning bitta o'rni (ikkinchisi bo'sh qolsin)
    if not one_per_desk and room.seats_per_desk > 1 and len(students) <= len([s for s in seats if s[2] == 1]):
        firsts = [s for s in seats if s[2] == 1]
        # Qatorlarda navbatma-navbat chap/o'ng — diagonal qo'shnilar ham kamayadi
        alt = [(r, d, 1 if r % 2 else min(2, room.seats_per_desk)) for (r, d, _) in firsts]
        alt = [s for s in alt if s in set(seats)]
        pool = alt if len(alt) >= len(students) else firsts
        seats = pool + [s for s in seats if s not in set(pool)]
    if len(students) > len(seats):
        raise ValueError(f"«{room.name}» xonasiga sig'maydi")
    # Navbatma-navbat (eng katta maktabdan) tartib — boshlang'ich holat
    by_school: dict[int, list[int]] = defaultdict(list)
    for pid, sch in students:
        by_school[sch].append(pid)
    order: list[tuple[int, int]] = []
    queues = sorted(by_school.items(), key=lambda kv: -len(kv[1]))
    while any(q for _, q in queues):
        for sch, q in queues:
            if q:
                order.append((q.pop(), sch))
    # «Ilon izi»: juft qatorlarda o'ngdan chapga
    used = sorted(seats[:len(order)], key=lambda s: (s[0], (s[1], s[2]) if s[0] % 2 else (-s[1], -s[2])))
    place: dict[Seat, Optional[tuple[int, int]]] = {s: None for s in seats}
    for seat, st_ in zip(used, order):
        place[seat] = st_
    all_place: dict[Seat, Optional[tuple[int, int]]] = dict(place)
    for s, v in fixed.items():
        all_place[s] = v
    nb = {s: neighbors(room, s) for s in all_place}

    def cost_at(seat: Seat, school: int) -> int:
        c = 0
        for n, w in nb[seat]:
            o = all_place.get(n)
            if o is not None and o[1] == school:
                c += w
        return c

    movable = list(place.keys())
    if not movable:
        return {}, total_penalty(room, all_place)
    # Mahalliy qidiruv: konfliktli o'quvchi → eng yaxshi almashinuv
    iters = iterations or max(2000, 60 * len(movable))
    stall = 0
    for _ in range(iters):
        conflicted = [s for s in movable if place[s] is not None and cost_at(s, place[s][1]) > 0]
        if not conflicted:
            break
        a = rng.choice(conflicted)
        sa = place[a]
        best, best_delta = None, 0
        base_a = cost_at(a, sa[1])
        for b in movable:
            if b == a:
                continue
            sb = place[b]
            if sb is not None and sb[1] == sa[1]:
                continue
            # Almashtirgandan keyingi o'zgarish (a va b qo'shni bo'lsa ham to'g'ri hisoblash uchun vaqtincha almashtiramiz)
            before = base_a + (cost_at(b, sb[1]) if sb else 0)
            all_place[a], all_place[b] = sb, sa
            after = cost_at(b, sa[1]) + (cost_at(a, sb[1]) if sb else 0)
            all_place[a], all_place[b] = sa, sb
            delta = after - before
            if delta < best_delta or (delta == best_delta and best is not None and rng.random() < 0.3):
                best, best_delta = b, delta
        if best is not None and best_delta < 0:
            place[a], place[best] = place[best], place[a]
            all_place[a], all_place[best] = place[a], place[best]
            stall = 0
        else:
            stall += 1
            if best is not None and best_delta == 0 and rng.random() < 0.5:  # «plato» bo'ylab siljish
                place[a], place[best] = place[best], place[a]
                all_place[a], all_place[best] = place[a], place[best]
            if stall > 4 * len(movable) + 50:
                break
    result = {v[0]: s for s, v in place.items() if v is not None}
    return result, total_penalty(room, all_place)


def total_penalty(room: Room, placed: dict[Seat, Optional[tuple[int, int]]]) -> int:
    """Bir maktabdan qo'shni o'tirganlar jarimasi (har juftlik bir marta)."""
    total = 0
    for s, v in placed.items():
        if v is None:
            continue
        for n, w in neighbors(room, s):
            o = placed.get(n)
            if o is not None and o[1] == v[1] and n > s:
                total += w
    return total


def conflict_pairs(room: Room, placed: dict[Seat, tuple[int, int]]) -> list[tuple[Seat, Seat, int]]:
    out = []
    for s, v in placed.items():
        for n, w in neighbors(room, s):
            o = placed.get(n)
            if o is not None and o[1] == v[1] and n > s and w >= W_FRONT:
                out.append((s, n, w))
    return out


# ---------------------------------------------------------------------------
# 1–2-bosqich: smena va xonalarga bo'lish
# ---------------------------------------------------------------------------
@dataclass
class Student:
    id: int
    school: int
    group: str = ""
    order: int = 0  # maktabning ro'yxatdagi tartib raqami («maktablar tartibida» rejimi uchun)


def _split_even(n: int, caps: list[int]) -> list[int]:
    """n ta o'quvchini sig'imlarga mutanosib bo'lish (eng katta qoldiq usuli)."""
    total = sum(caps)
    if total == 0:
        return [0] * len(caps)
    raw = [n * c / total for c in caps]
    base = [min(c, int(math.floor(x))) for x, c in zip(raw, caps)]
    rest = n - sum(base)
    order = sorted(range(len(caps)), key=lambda i: -(raw[i] - math.floor(raw[i])))
    while rest > 0:
        moved = False
        for i in order:
            if rest and base[i] < caps[i]:
                base[i] += 1
                rest -= 1
                moved = True
        if not moved:
            break
    return base


def spread(students: list[Student], slots: list[tuple[int, int]]) -> dict[int, list[Student]]:
    """Xonalarga taqsimlash: slots = [(xona_id, nechta)]. Har maktab xonalarga tekis tarqaladi."""
    remaining = {rid: q for rid, q in slots}
    cnt: dict[int, Counter] = {rid: Counter() for rid, _ in slots}
    out: dict[int, list[Student]] = {rid: [] for rid, _ in slots}
    by_school: dict[int, list[Student]] = defaultdict(list)
    for s in students:
        by_school[s.school].append(s)
    for sch, group in sorted(by_school.items(), key=lambda kv: -len(kv[1])):
        for s in group:
            cands = [rid for rid, _ in slots if remaining[rid] > 0]
            if not cands:
                raise ValueError("Xonalarda joy yetmadi")
            qty = dict(slots)
            rid = min(cands, key=lambda r: (cnt[r][sch] / max(qty[r], 1), cnt[r][sch], -remaining[r]))
            out[rid].append(s)
            cnt[rid][sch] += 1
            remaining[rid] -= 1
    return out


@dataclass
class PlanResult:
    assignments: dict[int, tuple[int, int, Seat]] = field(default_factory=dict)  # pid → (smena, xona, o'rin)
    penalty: int = 0
    room_penalty: dict[tuple[int, int], int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def split_by_school_order(students: list[Student], n_sessions: int, cap: int) -> list[list[Student]]:
    """Maktablar tartibida smenalarga: 1-smena — birinchi maktablar, keyingisi — keyingilari.

    Har maktab o'rtasi qaysi smena «ulushiga» tushsa, o'sha smenaga to'liq beriladi (maktab bo'linmaydi).
    Smena sig'imidan oshsa — ortiqchasi keyingi smenaga suriladi (faqat shunda maktab bo'linadi).
    """
    by_school: dict[int, list[Student]] = defaultdict(list)
    order: dict[int, int] = {}
    for s in students:
        by_school[s.school].append(s)
        order[s.school] = s.order
    schools = sorted(by_school, key=lambda k: (order[k], k))
    N = len(students)
    chunks: list[list[Student]] = [[] for _ in range(n_sessions)]
    cum = 0
    for sch in schools:
        grp = sorted(by_school[sch], key=lambda x: x.id)
        mid = cum + len(grp) / 2
        idx = min(n_sessions - 1, int(mid * n_sessions / N)) if N else 0
        chunks[idx].extend(grp)
        cum += len(grp)
    # Sig'imdan oshganini keyingi smenaga (oxirgisidan — oldingiga) surish, tartib saqlanadi
    for i in range(n_sessions - 1):
        if len(chunks[i]) > cap:
            extra = chunks[i][cap:]
            chunks[i] = chunks[i][:cap]
            chunks[i + 1] = extra + chunks[i + 1]
    for i in range(n_sessions - 1, 0, -1):
        if len(chunks[i]) > cap:
            extra = chunks[i][:len(chunks[i]) - cap]
            chunks[i] = chunks[i][len(chunks[i]) - cap:]
            chunks[i - 1] = chunks[i - 1] + extra
    return chunks


def plan(students: list[Student], sessions: list[ExamSession], rooms: list[Room], one_per_desk: bool,
         seed: Optional[int] = None, school_order: bool = False) -> PlanResult:
    """To'liq taqsimot (sof hisob — bazaga yozmaydi)."""
    rng = random.Random(seed)
    res = PlanResult()
    caps = {r.id: len(room_seats(r, one_per_desk)) for r in rooms}
    per_session = sum(caps.values())
    if not sessions or per_session == 0:
        raise ValueError("Smena yoki xona yo'q")
    if len(students) > per_session * len(sessions):
        need = math.ceil(len(students) / per_session)
        raise ValueError(f"Joy yetmaydi: {len(students)} o'quvchi, {per_session * len(sessions)} o'rin "
                         f"({len(sessions)} smena × {per_session}). Kamida {need} smena yoki ko'proq xona kerak.")
    # 1) Smenalar: guruh bo'yicha tartiblab, teng bo'laklarga
    students = sorted(students, key=lambda s: (s.group, s.school, s.id))
    loads = _split_even(len(students), [per_session] * len(sessions))
    chunks, i = [], 0
    if school_order:
        chunks = split_by_school_order(students, len(sessions), per_session)
        # Smena ichida — guruhsiz: maktablar xonalarga tekis tarqatiladi
        chunks = [[Student(s.id, s.school, "", s.order) for s in ch] for ch in chunks]
    elif any(s.group for s in students):
        for n in loads:
            chunks.append(students[i:i + n])
            i += n
    else:  # guruhsiz — har maktab smenalarga ham tekis tarqaladi
        chunks = [[] for _ in sessions]
        by_school: dict[int, list[Student]] = defaultdict(list)
        for s in students:
            by_school[s.school].append(s)
        k = 0
        for _, grp in sorted(by_school.items(), key=lambda kv: -len(kv[1])):
            for s in grp:
                # navbatdagi bo'sh joyi bor smena
                for _ in range(len(sessions)):
                    if len(chunks[k % len(sessions)]) < loads[k % len(sessions)]:
                        break
                    k += 1
                chunks[k % len(sessions)].append(s)
                k += 1
    room_list = [r for r in rooms if caps[r.id] > 0]
    for sess, chunk in zip(sessions, chunks):
        if not chunk:
            continue
        targets = _split_even(len(chunk), [caps[r.id] for r in room_list])
        # 2) Xonalar: guruhlar ketma-ket xonalarni egallaydi, guruh ichida maktablar tarqatiladi
        groups: dict[str, list[Student]] = defaultdict(list)
        for s in chunk:
            groups[s.group].append(s)
        room_iter = [[r.id, t] for r, t in zip(room_list, targets) if t > 0]
        per_room: dict[int, list[Student]] = defaultdict(list)
        ri = 0
        for gname in sorted(groups):
            members = groups[gname]
            need, slots = len(members), []
            while need > 0 and ri < len(room_iter):
                take = min(need, room_iter[ri][1])
                slots.append((room_iter[ri][0], take))
                room_iter[ri][1] -= take
                need -= take
                if room_iter[ri][1] == 0:
                    ri += 1
            for rid, lst in spread(members, slots).items():
                per_room[rid].extend(lst)
        # 3) O'rindiqlar
        for r in room_list:
            lst = per_room.get(r.id) or []
            if not lst:
                continue
            seats, pen = arrange_room(r, [(s.id, s.school) for s in lst], one_per_desk, rng)
            for pid, seat in seats.items():
                res.assignments[pid] = (sess.id, r.id, seat)
            res.room_penalty[(sess.id, r.id)] = pen
            res.penalty += pen
    return res


# ---------------------------------------------------------------------------
# Baza bilan ishlash
# ---------------------------------------------------------------------------
async def active_rooms(session: AsyncSession) -> list[Room]:
    return (await session.execute(select(Room).where(Room.is_active == True)  # noqa: E712
                                  .order_by(Room.position, Room.id))).scalars().all()


async def round_sessions(session: AsyncSession, round_id: int) -> list[ExamSession]:
    return (await session.execute(select(ExamSession).where(ExamSession.round_id == round_id,
                                                            ExamSession.is_active == True)  # noqa: E712
                                  .order_by(ExamSession.day, ExamSession.start_time, ExamSession.position,
                                            ExamSession.id))).scalars().all()


async def eligible_students(session: AsyncSession, round_: Round) -> list[Participant]:
    return (await session.execute(select(Participant).where(ranking.eligible_condition(round_))
                                  .order_by(Participant.id))).scalars().all()


def _group_key(p: Participant, how: str) -> str:
    if how == "grade":
        return f"{(p.grade.position if p.grade else 0):04d}"
    if how == "subject":
        return ",".join(str(s.id) for s in sorted(p.subjects, key=lambda s: s.id))
    return ""


def _natural(name: str) -> tuple:
    """«2-maktab» < «10-maktab» (raqamlar son sifatida taqqoslanadi)."""
    import re as _re
    return tuple(int(t) if t.isdigit() else t.lower() for t in _re.split(r"(\d+)", name or ""))


async def school_ranks(session: AsyncSession) -> dict[int, int]:
    """Maktablarning tartib raqami: «Maktablar» ro'yxatidagi tartib, teng bo'lsa — nomidagi raqam bo'yicha."""
    from ..models import School
    schools = (await session.execute(select(School))).scalars().all()
    ordered = sorted(schools, key=lambda x: (x.position, _natural(x.name), x.id))
    return {sc.id: i for i, sc in enumerate(ordered)}


async def assignments(session: AsyncSession, round_id: int) -> list[SeatAssignment]:
    return (await session.execute(select(SeatAssignment).where(SeatAssignment.round_id == round_id))).scalars().all()


async def stats(session: AsyncSession, round_: Round) -> dict:
    rooms = await active_rooms(session)
    sessions = await round_sessions(session, round_.id)
    one = st.get("seating_one_per_desk")
    per_session = sum(len(room_seats(r, one)) for r in rooms)
    eligible = (await session.execute(select(func.count()).select_from(Participant)
                                      .where(ranking.eligible_condition(round_)))).scalar_one()
    assigned = (await session.execute(
        select(func.count()).select_from(SeatAssignment).join(Participant, Participant.id == SeatAssignment.participant_id)
        .where(SeatAssignment.round_id == round_.id, ranking.eligible_condition(round_)))).scalar_one()
    sent = (await session.execute(select(func.count()).select_from(SeatAssignment).where(
        SeatAssignment.round_id == round_.id, SeatAssignment.pass_sent_at.is_not(None)))).scalar_one()
    stale = (await session.execute(
        select(func.count()).select_from(SeatAssignment).join(Participant, Participant.id == SeatAssignment.participant_id)
        .where(SeatAssignment.round_id == round_.id, ~ranking.eligible_condition(round_)))).scalar_one()
    return {"rooms": len(rooms), "sessions": len(sessions), "per_session": per_session,
            "capacity": per_session * len(sessions), "eligible": eligible, "assigned": assigned,
            "unassigned": max(0, eligible - assigned), "sent": sent, "stale": stale,
            "need_sessions": math.ceil(eligible / per_session) if per_session else 0}


async def run_full(session: AsyncSession, round_: Round, seed: Optional[int] = None) -> PlanResult:
    """To'liq taqsimlash (oldingi taqsimot o'chiriladi). Ruxsatnoma yuborilgan bo'lsa — ruxsat yo'q."""
    sent = (await session.execute(select(func.count()).select_from(SeatAssignment).where(
        SeatAssignment.round_id == round_.id, SeatAssignment.pass_sent_at.is_not(None)))).scalar_one()
    if sent:
        raise ValueError(f"{sent} ta o'quvchiga ruxsatnoma yuborilgan — to'liq qayta taqsimlab bo'lmaydi. "
                         "Yangi o'quvchilar uchun «Joylanmaganlarni joylash»dan foydalaning.")
    people = await eligible_students(session, round_)
    how = st.get("seating_group_by") or "none"
    ranks = await school_ranks(session)
    students = [Student(p.id, p.school_id, "" if how == "school_order" else _group_key(p, how),
                        ranks.get(p.school_id, 0)) for p in people]
    sessions = await round_sessions(session, round_.id)
    rooms = await active_rooms(session)
    import asyncio
    res = await asyncio.to_thread(plan, students, sessions, rooms, bool(st.get("seating_one_per_desk")), seed,
                                  how == "school_order")
    await session.execute(delete(SeatAssignment).where(SeatAssignment.round_id == round_.id))
    for pid, (sid, rid, seat) in res.assignments.items():
        session.add(SeatAssignment(participant_id=pid, round_id=round_.id, session_id=sid, room_id=rid,
                                   row=seat[0], desk=seat[1], seat=seat[2]))
    await session.flush()
    return res


async def occupancy(session: AsyncSession, round_id: int) -> dict[tuple[int, int], dict[Seat, SeatAssignment]]:
    """(smena, xona) → {o'rin: biriktirish}."""
    out: dict[tuple[int, int], dict[Seat, SeatAssignment]] = defaultdict(dict)
    for a in await assignments(session, round_id):
        out[(a.session_id, a.room_id)][(a.row, a.desk, a.seat)] = a
    return out


async def place_missing(session: AsyncSession, round_: Round) -> tuple[int, int, list[str]]:
    """Joylanmagan (keyin qo'shilgan) o'quvchilarni bo'sh joylarga qo'yadi, boshqalarga tegmaydi.

    Chetlatilgan/tushib qolganlarning o'rni bo'shatiladi. Natija: (joylandi, bo'shatildi, xatolar).
    """
    # 1) Endi qatnashmaydiganlar o'rnini bo'shatish
    stale_ids = (await session.execute(
        select(SeatAssignment.id).join(Participant, Participant.id == SeatAssignment.participant_id)
        .where(SeatAssignment.round_id == round_.id, ~ranking.eligible_condition(round_)))).scalars().all()
    if stale_ids:
        await session.execute(delete(SeatAssignment).where(SeatAssignment.id.in_(stale_ids)))
        await session.flush()
    people = await eligible_students(session, round_)
    have = {a.participant_id for a in await assignments(session, round_.id)}
    todo = [p for p in people if p.id not in have]
    if not todo:
        return 0, len(stale_ids), []
    sessions = await round_sessions(session, round_.id)
    rooms = await active_rooms(session)
    one = bool(st.get("seating_one_per_desk"))
    occ = await occupancy(session, round_.id)
    errors, placed = [], 0
    for p in todo:
        best = None
        for sess in sessions:
            for r in rooms:
                taken = occ.get((sess.id, r.id), {})
                free = [s for s in room_seats(r, one) if s not in taken]
                if not free:
                    continue
                same = sum(1 for a in taken.values() if a.participant.school_id == p.school_id)
                load = len(taken) / max(1, len(room_seats(r, one)))
                for s in free:
                    pen = sum(w for n, w in neighbors(r, s)
                              if n in taken and taken[n].participant.school_id == p.school_id)
                    key = (pen, same, round(load, 3), sess.day, sess.start_time, r.position, r.id, s)
                    if best is None or key < best[0]:
                        best = (key, sess, r, s)
        if best is None:
            errors.append(f"{p.full_name} (ID {p.code}): bo'sh joy qolmagan")
            continue
        _, sess, r, s = best
        a = SeatAssignment(participant_id=p.id, round_id=round_.id, session_id=sess.id, room_id=r.id,
                           row=s[0], desk=s[1], seat=s[2])
        a.participant = p
        session.add(a)
        occ.setdefault((sess.id, r.id), {})[s] = a
        placed += 1
    await session.flush()
    return placed, len(stale_ids), errors


async def move(session: AsyncSession, a: SeatAssignment, session_id: int, room_id: int,
               seat: Optional[Seat]) -> Optional[SeatAssignment]:
    """O'quvchini boshqa joyga ko'chirish. Joy band bo'lsa — almashtiriladi (ikkinchi o'quvchi qaytariladi).

    seat=None — shu xonadagi eng yaxshi bo'sh joy.
    """
    room = await session.get(Room, room_id)
    sess = await session.get(ExamSession, session_id)
    if room is None or sess is None or sess.round_id != a.round_id:
        raise ValueError("Xona yoki smena topilmadi")
    one = bool(st.get("seating_one_per_desk"))
    valid = set(room_seats(room, False))  # qo'lda ko'chirishda 2-o'rin ham mumkin
    occ = (await occupancy(session, a.round_id)).get((session_id, room_id), {})
    if seat is None:
        school = a.participant.school_id
        free = [s for s in room_seats(room, one) if s not in occ] or [s for s in valid if s not in occ]
        if not free:
            raise ValueError("Bu xonada bo'sh joy yo'q")
        seat = min(free, key=lambda s: (sum(w for n, w in neighbors(room, s)
                                             if n in occ and occ[n].participant_id != a.participant_id
                                             and occ[n].participant.school_id == school), s))
    if seat not in valid:
        raise ValueError("Bunday o'rin yo'q yoki o'chirilgan")
    other = occ.get(seat)
    if other is not None and other.id == a.id:
        return None
    old = (a.session_id, a.room_id, a.row, a.desk, a.seat)
    # Noyob cheklov to'qnashmasligi uchun avval vaqtinchalik joyga (-1) olib qo'yamiz
    a.row = -a.id
    await session.flush()
    if other is not None:
        other.session_id, other.room_id, other.row, other.desk, other.seat = old
        await session.flush()
    a.session_id, a.room_id, a.row, a.desk, a.seat = session_id, room_id, seat[0], seat[1], seat[2]
    await session.flush()
    return other


# ---------------------------------------------------------------------------
# Ruxsatnoma
# ---------------------------------------------------------------------------
def fmt_day(d: date, lang: str = "lat") -> str:
    s = f"{d.day}-{MONTHS[d.month - 1]} {d.year}"
    return lat2cyr(s) if lang == "cyr" else s


def weekday(d: date, lang: str = "lat") -> str:
    s = WEEKDAYS[d.weekday()]
    return lat2cyr(s) if lang == "cyr" else s


def end_time(sess: ExamSession) -> str:
    try:
        h, m = map(int, sess.start_time.split(":"))
    except ValueError:
        return ""
    t = datetime.combine(sess.day, time(h, m)) + timedelta(minutes=sess.duration_min or 0)
    return t.strftime("%H:%M")


def seat_text(room: Room, a: SeatAssignment, lang: str = "lat") -> str:
    no = seat_number(room, (a.row, a.desk, a.seat))
    side = ""
    if room.seats_per_desk == 2:
        side = ", chap" if a.seat == 1 else ", o'ng"
    elif room.seats_per_desk > 2:
        side = f", {a.seat}-o'rin"
    s = f"{no}-o'rin · {a.row}-qator, {a.desk}-parta{side}"
    return lat2cyr(s) if lang == "cyr" else s


def room_label(room: Room, lang: str = "lat") -> str:
    s = room.name if any(ch.isalpha() for ch in room.name) else f"{room.name}-xona"
    if room.floor:
        s += f" ({room.floor}-qavat)"
    return lat2cyr(s) if lang == "cyr" else s


async def current_assignment(session: AsyncSession, p: Participant) -> Optional[SeatAssignment]:
    cur = await ranking.current_round(session)
    if cur is None:
        return None
    return (await session.execute(select(SeatAssignment).where(
        SeatAssignment.participant_id == p.id, SeatAssignment.round_id == cur.id))).scalars().first()


def build_pass_context(p: Participant, a: SeatAssignment) -> dict:
    lang = p.lang
    sess, room = a.session, a.room
    b: Optional[Building] = room.building if room else None
    rnd = sess.round if sess else None
    return {
        "full_name": p.full_name, "first_name": p.first_name, "last_name": p.last_name,
        "school": by_lang(p.school.name, p.school.name_cyr, lang) if p.school else "",
        "grade": by_lang(p.grade.name, p.grade.name_cyr, lang) if p.grade else "",
        "code": p.code or "", "olympiad": st.get("olympiad_name"),
        "round": by_lang(rnd.name, rnd.name_cyr, lang) if rnd else "",
        "date": datetime.now(config.TZ).strftime("%d.%m.%Y"),
        "subjects": ", ".join(by_lang(s.name, s.name_cyr, lang) for s in p.subjects),
        "exam_date": fmt_day(sess.day, lang), "weekday": weekday(sess.day, lang),
        "arrive_time": sess.arrive_time, "start_time": sess.start_time, "end_time": end_time(sess),
        "duration": f"{sess.duration_min} daqiqa" if lang != "cyr" else f"{sess.duration_min} дақиқа",
        "session": by_lang(sess.name, sess.name_cyr, lang),
        "building": by_lang(b.name, b.name_cyr, lang) if b else "",
        "address": by_lang(b.address, b.address_cyr, lang) if b else "",
        "room": room_label(room, lang), "floor": room.floor, "row": str(a.row), "desk": str(a.desk),
        "seat_side": ("chap" if a.seat == 1 else "o'ng") if room.seats_per_desk == 2 else str(a.seat),
        "seat_no": str(seat_number(room, (a.row, a.desk, a.seat))),
        "seat_text": seat_text(room, a, lang),
    }


async def pass_context(session: AsyncSession, p: Participant) -> Optional[dict]:
    a = await current_assignment(session, p)
    return build_pass_context(p, a) if a else None


async def pass_template(session: AsyncSession):
    from ..models import CertificateTemplate
    return (await session.execute(select(CertificateTemplate).where(
        CertificateTemplate.kind == "pass", CertificateTemplate.is_active == True)  # noqa: E712
        .order_by(CertificateTemplate.position, CertificateTemplate.id))).scalars().first()


def pass_caption(p: Participant, ctx: dict, map_url: str = "") -> str:
    from html import escape
    from ..bot.texts import t
    e = {k: escape(str(v), quote=False) for k, v in ctx.items()}
    text = t("pass_caption", p.lang).format(
        olympiad=e["olympiad"], name=e["full_name"], code=e["code"], date=e["exam_date"], weekday=e["weekday"],
        arrive=e["arrive_time"], start=e["start_time"], building=e["building"], address=e["address"],
        room=e["room"], seat=e["seat_text"])
    if map_url and map_url.startswith(("http://", "https://")):
        text += f'\n🗺 <a href="{escape(map_url, quote=True)}">{escape(t("pass_map", p.lang))}</a>'
    return text[:1024]


async def send_pass(bot, session: AsyncSession, p: Participant, changed: bool = False) -> str:
    """Bitta o'quvchiga ruxsatnoma (rasm + PDF). 'ok' | 'blocked' | 'fail' | 'skip'."""
    import asyncio
    from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
    from aiogram.types import BufferedInputFile
    from . import certificates as certs
    from . import design as dz
    from ..bot.texts import t

    a = await current_assignment(session, p)
    tpl = await pass_template(session)
    if a is None or tpl is None or p.status != "active":
        return "skip"
    ctx = build_pass_context(p, a)
    img = await asyncio.to_thread(certs.render_template, tpl, ctx)
    jpg = await asyncio.to_thread(dz.to_jpeg, img)
    pdf = await asyncio.to_thread(dz.to_pdf, img, certs.template_dpi(tpl))
    caption = pass_caption(p, ctx, a.room.building.map_url if a.room and a.room.building else "")
    for attempt in range(3):
        try:
            if changed:
                await bot.send_message(p.telegram_id, t("pass_changed", p.lang))
            await bot.send_photo(p.telegram_id, BufferedInputFile(jpg, f"ruxsatnoma_{p.code}.jpg"), caption=caption)
            await bot.send_document(p.telegram_id, BufferedInputFile(pdf, f"ruxsatnoma_{p.code}.pdf"))
            a.pass_sent_at = now_utc()
            return "ok"
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
            changed = False
        except TelegramForbiddenError:
            p.bot_blocked = True
            return "blocked"
        except TelegramBadRequest:
            return "fail"
        except Exception:
            await asyncio.sleep(2)
    return "fail"


def pass_send_at(day: date) -> datetime:
    """Ruxsatnoma yuboriladigan vaqt (UTC): olimpiadadan bir kun oldin, sozlamadagi soatda."""
    raw = st.get("pass_send_time") or "18:00"
    try:
        h, m = map(int, raw.split(":"))
        h, m = max(0, min(23, h)), max(0, min(59, m))
    except ValueError:
        h, m = 18, 0
    local = datetime.combine(day - timedelta(days=1), time(h, m), tzinfo=config.TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def school_names(people: Iterable[Participant]) -> dict[int, str]:
    return {p.school_id: (p.school.name if p.school else "") for p in people}

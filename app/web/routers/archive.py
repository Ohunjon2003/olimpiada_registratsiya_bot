"""Admin: olimpiada yakunida — hamma ma'lumotni bitta Excel'ga yuklash va eski ma'lumotlarni tozalash."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import config
from ...db import get_session
from ...models import (Appeal, Attendance, AuditLog, Block, Broadcast,
                       AnswerKey, CustomQuestion, FsmRecord, Participant, Result, Round,
                       SeatAssignment, SheetScan, Subject, User)
from ...security import verify_password
from ...services import attendance as att_svc
from ...services import audit, backup, export, notify, ranking
from ...services import settings as st
from ...utils import fmt_dt, now_utc
from ..core import client_ip, flash, render, require_admin, verify_csrf

log = logging.getLogger("archive")
router = APIRouter(prefix="/admin/archive", dependencies=[Depends(verify_csrf)])

CONFIRM_WORD = "TOZALASH"


async def _counts(session: AsyncSession) -> dict:
    async def n(model):
        return (await session.execute(select(func.count()).select_from(model))).scalar_one()
    db_size = sum(p.stat().st_size for p in config.DATA_DIR.glob("olimpiada.db*") if p.exists())
    return {"participants": await n(Participant), "results": await n(Result), "attendance": await n(Attendance),
            "appeals": await n(Appeal), "broadcasts": await n(Broadcast), "audit": await n(AuditLog),
            "fsm": await n(FsmRecord), "seats": await n(SeatAssignment), "scans": await n(SheetScan), "db_mb": round(db_size / 1024 / 1024, 2)}


@router.get("")
async def archive_page(request: Request, user: User = Depends(require_admin),
                       session: AsyncSession = Depends(get_session)):
    return render(request, "admin/archive.html", {"c": await _counts(session), "word": CONFIRM_WORD,
                                                  "has_tg": bool(notify.admin_chats())})


# ---------------------------------------------------------------------------
# To'liq Excel
# ---------------------------------------------------------------------------
async def build_archive(session: AsyncSession) -> bytes:
    """Barcha ma'lumot bitta Excel faylda: xulosa, ishtirokchilar, har tur/fan/blok reytingi,
    barcha natijalar, davomat, e'tirozlar."""
    sheets: list[dict] = []
    cur = await ranking.current_round(session)
    rounds = (await session.execute(select(Round).order_by(Round.position, Round.id))).scalars().all()
    subjects = (await session.execute(select(Subject).order_by(Subject.position, Subject.id))).scalars().all()
    blocks = (await session.execute(select(Block).order_by(Block.position, Block.id))).scalars().all()
    people = (await session.execute(select(Participant).order_by(Participant.last_name,
                                                                 Participant.first_name))).scalars().all()
    questions = (await session.execute(select(CustomQuestion).order_by(CustomQuestion.position,
                                                                       CustomQuestion.id))).scalars().all()
    by_grade = bool(st.get("ranking_by_grade"))

    # 1) Xulosa
    summary = [["Olimpiada", st.get("olympiad_name")], ["Eksport vaqti", fmt_dt(now_utc())],
               ["Ishtirokchilar", len(people)],
               ["Faol", sum(1 for p in people if p.status == "active")],
               ["Reyting", "sinf kesimida" if by_grade else "umumiy"]]
    for r in rounds:
        c = await att_svc.counts(session, r)
        n_res = (await session.execute(select(func.count()).select_from(Result)
                                       .where(Result.round_id == r.id))).scalar_one()
        summary.append([f"{r.name}: qatnashishi kerak / keldi / kelmadi / natijalar",
                        f"{c['total']} / {c['came']} / {c['absent']} / {n_res}"])
    for s in subjects:
        summary.append([f"Fan: {s.name}", f"{sum(1 for p in people if any(x.id == s.id for x in p.subjects))} o'quvchi"])
    sheets.append({"title": "Xulosa", "headers": ["Ko'rsatkich", "Qiymat"], "rows": summary, "widths": [50, 40]})

    # 2) Ishtirokchilar (joriy tur davomati bilan)
    att = await att_svc.status_map(session, cur.id) if cur else None
    headers, rows, widths = export.participants_rows(people, questions, att)
    sheets.append({"title": "Ishtirokchilar", "headers": headers, "rows": rows, "widths": widths})

    # 3) Reytinglar: natijasi bor har bir tur × fan va blok
    multi = len(rounds) > 1
    for r in rounds:
        has = (await session.execute(select(func.count()).select_from(Result)
                                     .where(Result.round_id == r.id))).scalar_one()
        if not has:
            continue
        prefix = f"{r.name} " if multi else ""
        for s in subjects:
            rws = await ranking.subject_ranking(session, s, r, by_grade=by_grade, use_cache=False)
            if rws:
                h, o = export.subject_ranking_table(s, rws)
                sheets.append({"title": prefix + s.name, "headers": h, "rows": o, "places": True,
                               "widths": [7, 8, 18, 16, 26, 8]})
        for b in blocks:
            rws = await ranking.block_ranking(session, b, r, by_grade=by_grade, use_cache=False)
            if rws:
                h, o = export.block_ranking_table(b.subjects, rws, b.aggregation)
                sheets.append({"title": prefix + "Blok " + b.name, "headers": h, "rows": o, "places": True,
                               "widths": [7, 8, 18, 16, 26, 8]})

    # 4) Barcha natijalar (xom jadval)
    res = (await session.execute(select(Result).order_by(Result.round_id, Result.subject_id,
                                                         Result.participant_id))).scalars().all()
    subj_name = {s.id: s.name for s in subjects}
    round_name = {r.id: r.name for r in rounds}
    sheets.append({"title": "Barcha natijalar",
                   "headers": ["Tur", "Fan", "ID", "Familiya", "Ism", "Maktab", "Sinf", "To'g'ri javoblar",
                               "Ball", "Maks. ball", "Foiz", "Hakam", "Kiritilgan"],
                   "rows": [[round_name.get(x.round_id, ""), subj_name.get(x.subject_id, ""), x.participant.code,
                             x.participant.last_name, x.participant.first_name,
                             x.participant.school.name if x.participant.school else "",
                             x.participant.grade.name if x.participant.grade else "",
                             x.correct_total, x.score, x.max_score, round(x.percent, 2),
                             x.entered_by.username if x.entered_by else "", fmt_dt(x.updated_at)] for x in res],
                   "widths": [10, 18, 8, 18, 16, 26, 7, 10, 9, 9, 8, 14, 17]})

    # 5) Davomat
    att_rows = (await session.execute(select(Attendance, Participant)
                                      .join(Participant, Participant.id == Attendance.participant_id)
                                      .order_by(Attendance.round_id, Participant.last_name))).all()
    sheets.append({"title": "Davomat",
                   "headers": ["Tur", "ID", "Familiya", "Ism", "Maktab", "Sinf", "Holat", "Belgilagan", "Vaqt"],
                   "rows": [[round_name.get(a.round_id, ""), p.code, p.last_name, p.first_name,
                             p.school.name if p.school else "", p.grade.name if p.grade else "",
                             "Keldi" if a.present else "Kelmadi", a.marked_by, fmt_dt(a.marked_at)]
                            for a, p in att_rows],
                   "widths": [10, 8, 18, 16, 26, 7, 10, 14, 17]})

    # 6) Joylashtirish (o'rinlar)
    seats = (await session.execute(select(SeatAssignment))).scalars().all()
    seats.sort(key=lambda a: (a.round_id, a.session.day, a.session.start_time, a.room.position, a.room_id,
                              a.row, a.desk, a.seat))
    sheets.append({"title": "O'rinlar",
                   "headers": ["Tur", "Sana", "Smena", "Boshlanish", "Bino", "Xona", "Qator", "Parta", "O'rin", "ID",
                               "Familiya Ism", "Maktab", "Sinf", "Ruxsatnoma yuborilgan"],
                   "rows": [[round_name.get(a.round_id, ""), a.session.day.strftime("%d.%m.%Y"), a.session.name,
                             a.session.start_time, a.room.building.name if a.room.building else "", a.room.name,
                             a.row, a.desk, a.seat, a.participant.code, a.participant.full_name,
                             a.participant.school.name if a.participant.school else "",
                             a.participant.grade.name if a.participant.grade else "", fmt_dt(a.pass_sent_at)]
                            for a in seats],
                   "widths": [10, 11, 10, 10, 22, 10, 7, 7, 7, 8, 26, 26, 7, 18]})

    # 7) Skanerlangan titullar
    scans = (await session.execute(select(SheetScan).order_by(SheetScan.id))).scalars().all()
    st_names2 = {"applied": "Qabul qilingan", "review": "Tekshirish kerak", "rejected": "Rad etilgan",
                 "superseded": "Almashtirilgan"}
    sheets.append({"title": "Titullar",
                   "headers": ["№", "Tur", "Fan", "Variant", "ID", "Familiya Ism", "Belgilangan ID", "Javoblar",
                               "To'g'ri", "Ball", "Foiz", "Holat", "Skaner qilgan", "Vaqt"],
                   "rows": [[x.id, round_name.get(x.round_id, ""), x.subject.name if x.subject else "", "ABCD"[x.variant],
                             x.participant.code, x.participant.full_name, x.id_marked, x.answers, x.correct, x.score,
                             x.percent, st_names2.get(x.status, x.status), x.scanned_by, fmt_dt(x.scanned_at)]
                            for x in scans],
                   "widths": [6, 10, 16, 8, 8, 24, 10, 40, 8, 8, 8, 14, 14, 17]})

    # 8) E'tirozlar
    appeals = (await session.execute(select(Appeal).order_by(Appeal.id))).scalars().all()
    st_names = {"new": "Yangi", "accepted": "Qabul qilingan", "rejected": "Rad etilgan"}
    sheets.append({"title": "E'tirozlar",
                   "headers": ["№", "ID", "Familiya Ism", "Fan", "Tur", "E'tiroz", "Holat", "Javob", "Javob bergan",
                               "Yozilgan", "Javob vaqti"],
                   "rows": [[a.id, a.participant.code, a.participant.full_name, a.subject.name if a.subject else "",
                             a.round.name if a.round else "", a.text, st_names.get(a.status, a.status), a.answer,
                             a.answered_by, fmt_dt(a.created_at), fmt_dt(a.answered_at)] for a in appeals],
                   "widths": [6, 8, 24, 16, 10, 50, 14, 40, 14, 17, 17]})
    return await asyncio.to_thread(export.workbook_from_sheets, sheets)


@router.get("/export")
async def archive_export(request: Request, user: User = Depends(require_admin),
                         session: AsyncSession = Depends(get_session)):
    data = await build_archive(session)
    name = f"olimpiada_toliq_{datetime.now(config.TZ):%Y%m%d_%H%M}.xlsx"
    audit.log(session, user, "export", "archive", "", "To'liq Excel eksport", ip=client_ip(request))
    await session.commit()
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# Tozalash
# ---------------------------------------------------------------------------
def _vacuum() -> None:
    """Bo'shagan joyni diskka qaytarish (baza fayli kichrayadi)."""
    con = sqlite3.connect(config.DB_PATH, timeout=60)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
    finally:
        con.close()


@router.post("/cleanup")
async def archive_cleanup(request: Request, user: User = Depends(require_admin),
                          session: AsyncSession = Depends(get_session)):
    form = await request.form()
    what = set(form.getlist("what"))
    if str(form.get("confirm") or "").strip().upper() != CONFIRM_WORD:
        flash(request, f"Tasdiqlash uchun «{CONFIRM_WORD}» so'zini yozing.", "error")
        return RedirectResponse("/admin/archive#cleanup", 303)
    if not verify_password(str(form.get("password") or ""), user.password_hash):
        flash(request, "Parol noto'g'ri.", "error")
        return RedirectResponse("/admin/archive#cleanup", 303)
    if not what:
        flash(request, "Nimani tozalashni belgilang.", "error")
        return RedirectResponse("/admin/archive#cleanup", 303)

    # 1) Avval albatta zaxira nusxa (xato bo'lsa — hech narsa o'chirilmaydi)
    path = await asyncio.to_thread(backup.make_backup, "tozalashdan_oldin")
    sent = await notify.send_backup(path, f"💾 Tozalashdan oldingi zaxira: {path.name}") \
        if notify.admin_chats() else 0

    before = await _counts(session)
    done = []
    def drop_scan_files() -> int:
        n = 0
        for f in (config.UPLOAD_DIR / "scans").glob("*.jpg"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
        return n

    if "participants" in what or "results" in what:
        await session.execute(delete(SheetScan))
        await session.execute(delete(AnswerKey))
        await asyncio.to_thread(drop_scan_files)
    if "participants" in what:
        # Natijalar, davomat, e'tirozlar, javoblar bazadagi CASCADE orqali birga o'chadi
        await session.execute(delete(Participant))
        await session.execute(delete(FsmRecord))  # botdagi chala qolgan holatlar
        done.append(f"{before['participants']} ta ishtirokchi (natija, davomat, e'tiroz va o'rinlari bilan)")
    elif "results" in what:
        await session.execute(delete(Result))
        await session.execute(delete(Attendance))
        await session.execute(delete(Appeal))
        await session.execute(delete(SeatAssignment))
        done.append(f"{before['results']} ta natija, {before['attendance']} ta davomat, {before['appeals']} ta e'tiroz, "
                    f"{before['seats']} ta o'rin, {before['scans']} ta skanerlangan titul (kalitlar bilan)")
    if "broadcasts" in what:
        await session.execute(delete(Broadcast))
        for f in (config.UPLOAD_DIR / "broadcasts").glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
        done.append(f"{before['broadcasts']} ta xabar tarixi")
    if "audit" in what:
        await session.execute(delete(AuditLog))
        done.append(f"{before['audit']} ta jurnal yozuvi")
    if "flags" in what:
        await st.set_many(session, {"results_published": False, "certificates_enabled": False,
                                    "appeals_enabled": False, "appeals_until": None,
                                    "registration_start": None, "registration_end": None,
                                    "passes_published": False})
        done.append("e'lonlar va muddatlar (natija, sertifikat, e'tiroz, ro'yxat muddati)")
    audit.log(session, user, "delete", "archive", "", "Tozalash: " + "; ".join(done),
              new={"backup": path.name, "what": sorted(what)}, ip=client_ip(request))
    await session.commit()
    ranking.invalidate()

    try:
        await asyncio.to_thread(_vacuum)
    except Exception as e:  # baza band bo'lsa — keyingi safar; ma'lumot baribir o'chirilgan
        log.warning("VACUUM bajarilmadi: %s", e)
    after = await _counts(session)
    flash(request, "✅ Tozalandi: " + "; ".join(done) + f". Baza hajmi: {before['db_mb']} → {after['db_mb']} MB. "
                   f"Oldingi holat zaxirada saqlandi: {path.name}"
                   + (" (Telegram'ga ham yuborildi)" if sent else ""))
    await notify.send(f"🧹 <b>Ma'lumotlar tozalandi</b> ({user.username})\n" + "\n".join("• " + d for d in done)
                      + f"\n💾 Zaxira: {path.name}")
    return RedirectResponse("/admin/archive", 303)

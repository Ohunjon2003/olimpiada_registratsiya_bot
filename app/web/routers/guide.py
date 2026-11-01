"""O'quvchilar uchun ochiq «Titulni to'ldirish qo'llanmasi».

Login talab qilinmaydi: havolani o'quvchilarga botda yoki guruhda yuborsa bo'ladi;
PNG/PDF ni yuklab olib, sinfda proyektor orqali ko'rsatish yoki chop etish ham mumkin.
Ichida shaxsiy ma'lumot yo'q (namuna ID, «NAMUNA» tamg'asi).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ...services import omr_guide
from ...services import settings as st
from ..core import render

router = APIRouter()


def _name() -> str:
    return (st.get("olympiad_name") or "Olimpiada")[:60]


@router.get("/qollanma", include_in_schema=False)
async def guide_page(request: Request):
    return render(request, "guide.html", {"olympiad": _name()})


@router.get("/qollanma.png", include_in_schema=False)
async def guide_png():
    return Response(omr_guide.guide_png(_name()), media_type="image/png",
                    headers={"Content-Disposition": 'inline; filename="titul-qollanma.png"', "Cache-Control": "public, max-age=300"})


@router.get("/qollanma.pdf", include_in_schema=False)
async def guide_pdf():
    return Response(omr_guide.guide_pdf(_name()), media_type="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="titul-qollanma.pdf"', "Cache-Control": "public, max-age=300"})

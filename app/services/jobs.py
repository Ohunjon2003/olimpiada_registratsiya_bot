"""Uzoq davom etadigan ishlar (1500 ta sertifikatni ZIP/PDF qilish va h.k.) — fonda.

Sahifa qotib qolmaydi: ish fonda bajariladi, sahifa har 2 soniyada holatini so'raydi,
tayyor bo'lgach yuklab olish tugmasi chiqadi. Natija fayllari data/exports/ da 1 kun saqlanadi.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from ..config import config

log = logging.getLogger("jobs")
KEEP_SECONDS = 24 * 3600


@dataclass
class Job:
    id: str
    title: str
    owner: int
    total: int = 0
    done: int = 0
    status: str = "running"  # running | done | error
    error: str = ""
    path: Optional[Path] = None
    filename: str = ""
    media_type: str = "application/octet-stream"
    started: float = field(default_factory=time.time)

    @property
    def percent(self) -> int:
        return int(self.done * 100 / self.total) if self.total else (100 if self.status == "done" else 0)


_jobs: dict[str, Job] = {}
_tasks: set[asyncio.Task] = set()


def exports_dir() -> Path:
    d = config.DATA_DIR / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup() -> None:
    """Eski natija fayllari va ishlarni o'chirish."""
    now = time.time()
    for p in exports_dir().glob("*"):
        try:
            if now - p.stat().st_mtime > KEEP_SECONDS:
                p.unlink()
        except OSError:
            pass
    for jid in [j for j, job in _jobs.items() if now - job.started > KEEP_SECONDS]:
        _jobs.pop(jid, None)


def start(title: str, owner: int, worker: Callable[[Job], Awaitable[None]]) -> Job:
    """Ishni fonda boshlaydi. worker(job) — job.total/done ni yangilab boradi va job.path ni to'ldiradi."""
    _cleanup()
    job = Job(id=secrets.token_hex(8), title=title, owner=owner)
    _jobs[job.id] = job

    async def run() -> None:
        try:
            await worker(job)
            job.status = "done" if job.path else "error"
            if not job.path and not job.error:
                job.error = "Hech narsa topilmadi"
        except Exception as e:  # xatoni foydalanuvchiga ko'rsatamiz
            log.exception("Fon ishi xatosi: %s", title)
            job.status, job.error = "error", f"{type(e).__name__}: {e}"

    task = asyncio.create_task(run(), name=f"job:{job.id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return job


def get(job_id: str, owner: int) -> Optional[Job]:
    job = _jobs.get(job_id)
    return job if job and job.owner == owner else None

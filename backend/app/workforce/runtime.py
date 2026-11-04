"""Firma module runtime: initialisation, background tasks, daily summary scheduler, API guard.

A worker (PIN) may only use attendance and their own tasks — WorkerGuard runs behind AuthMiddleware
(which sets the access scope) and forbids everything else under /api and /mcp."""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth import current_scope
from app.security import request_path
from app.workforce import store
from app.workforce.config import get_workforce_settings

logger = logging.getLogger(__name__)

_jobs: set[asyncio.Task[Any]] = set()


def init_workforce() -> None:
    from app.workforce.dialog import recover_stuck_reports

    get_workforce_settings().ensure_dirs()
    store.init_workforce_db()
    recover_stuck_reports()
    for src in store.list_sources():
        if src["status"] in ("waiting", "extracting"):
            schedule(_import_job, src["id"])


def _import_job(source_id: str) -> None:
    from app.workforce.importer import run_import

    run_import(source_id)


def schedule(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    """Runs blocking work (STT, LLM, PDF) in a thread and keeps a reference to the task."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    _jobs.add(task)
    task.add_done_callback(_jobs.discard)


async def wait_for_jobs() -> None:
    """For tests — waits until all background tasks finish."""
    while _jobs:
        await asyncio.gather(*list(_jobs), return_exceptions=True)


# --- worker API guard -------------------------------------------------------------------

WORKER_ALLOWED_PREFIXES = ("/api/attendance/", "/api/me/")
# only the endpoints a worker's phone needs — no API tokens, users or anything else
WORKER_ALLOWED_PATHS = {
    "/api/health",
    "/api/voice",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/me",
    "/api/auth/pin",
    "/api/auth/workers",
}
_CACHE_TTL_S = 5.0
_worker_cache: tuple[float, set[str]] = (0.0, set())


def _workers_cached() -> set[str]:
    global _worker_cache
    stamp, ids = _worker_cache
    if time.monotonic() - stamp > _CACHE_TTL_S:
        ids = store.worker_ids()
        _worker_cache = (time.monotonic(), ids)
    return ids


def invalidate_worker_cache() -> None:
    global _worker_cache
    _worker_cache = (0.0, set())


def is_worker(user_id: str | None) -> bool:
    return bool(user_id) and user_id in _workers_cached()


class WorkerGuard:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = request_path(scope)  # same root_path handling as AuthMiddleware
        if scope["type"] == "http" and (path.startswith("/api/") or path == "/mcp"):
            user_id = current_scope().user_id
            allowed = path in WORKER_ALLOWED_PATHS or path.startswith(WORKER_ALLOWED_PREFIXES)
            if user_id and not allowed and await run_in_threadpool(is_worker, user_id):
                response = JSONResponse(
                    {"detail": "pracovník má prístup len k dochádzke"}, status_code=403
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- scheduler --------------------------------------------------------------------------


def _summary_time() -> dtime:
    raw = get_workforce_settings().daily_summary_time
    try:
        hh, mm = raw.split(":")
        return dtime(int(hh), int(mm))
    except ValueError:
        logger.warning("RAG_DAILY_SUMMARY_TIME=%r nie je HH:MM — používam 19:00", raw)
        return dtime(19, 0)


def cleanup_audio(now: datetime | None = None) -> int:
    """Recordings older than RAG_AUDIO_RETENTION_DAYS are deleted; the transcript stays."""
    days = get_workforce_settings().audio_retention_days
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    removed = 0
    for row in store.expired_audio_inputs(cutoff.strftime("%Y-%m-%d %H:%M:%S")):
        Path(row["audio_path"]).unlink(missing_ok=True)
        store.clear_input_audio(row["id"])
        removed += 1
    return removed


def daily_tick(now: datetime) -> bool:
    """After the summary time: generate today's summary (if there are workers and something
    changed)."""
    from app.workforce.reports_pdf import render_daily_summary, summary_is_stale

    if not get_workforce_settings().daily_summary or now.time() < _summary_time():
        return False
    day = now.date().isoformat()
    reports = store.list_reports(day=day)
    weekend = now.date().weekday() >= 5
    if not store.list_workers(active_only=True) or (weekend and not reports):
        return False
    if not summary_is_stale(day):
        return False
    render_daily_summary(day)
    logger.info("súhrnný denný výpis %s vygenerovaný", day)
    return True


async def scheduler_loop(stop: asyncio.Event, interval_s: float = 60.0) -> None:
    last_cleanup: date | None = None
    while not stop.is_set():
        now = datetime.now()
        try:
            await asyncio.to_thread(daily_tick, now)
            if last_cleanup != now.date():
                await asyncio.to_thread(cleanup_audio)
                last_cleanup = now.date()
        except Exception:  # noqa: BLE001 — the scheduler must not crash
            logger.exception("plánovač dochádzky zlyhal")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)

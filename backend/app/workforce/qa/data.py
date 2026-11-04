"""Read-only access of the chat to company data — only allowed columns, never sensitive fields.

The chat gets data EXCLUSIVELY through this module:
- the connection is read-only (`mode=ro` + `PRAGMA query_only`), a write fails already in SQLite,
- SELECTs name columns explicitly (no `SELECT *`) — `pin_hash`, `password_hash`,
  `email`, sessions, API tokens and speech transcripts never get here; a test checks it,
- results are frozen DTOs that have no other fields at all.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from functools import cached_property
from typing import Any

from app.config import get_settings
from app.workforce.report_model import ReportData


@dataclass(frozen=True)
class WorkerRef:
    id: str
    name: str
    active: bool


@dataclass(frozen=True)
class SiteRef:
    id: str
    name: str
    active: bool


@dataclass(frozen=True)
class TaskFact:
    id: str
    site_id: str
    site_name: str
    title: str
    description: str
    status: str  # todo | in_progress | done | cancelled
    progress: int
    date_from: str | None
    date_to: str | None
    done_on: str | None
    depends_on: str | None
    assignees: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """The shape that `tasks.is_active_on` expects."""
        return {
            "id": self.id,
            "status": self.status,
            "done_on": self.done_on,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "depends_on": self.depends_on,
        }


@dataclass(frozen=True)
class ReportFact:
    user_id: str
    day: str
    status: str  # draft | processing | questioning | ready | submitted
    submitted_at: str | None
    data: ReportData

    @property
    def submitted(self) -> bool:
        return self.status == "submitted"


WORKERS_SQL = """SELECT u.id, u.name, w.active
  FROM wf_workers w JOIN users u ON u.id = w.user_id
  ORDER BY u.name COLLATE NOCASE"""
SITES_SQL = "SELECT s.id, s.name, s.active FROM wf_sites s ORDER BY s.name COLLATE NOCASE"
TASKS_SQL = """SELECT t.id, t.site_id, s.name AS site_name, t.title, t.description, t.status,
  t.progress, t.date_from, t.date_to, t.done_on, t.depends_on,
  (SELECT json_group_array(a.user_id) FROM wf_task_assignees a WHERE a.task_id = t.id)
    AS assignees
  FROM wf_tasks t JOIN wf_sites s ON s.id = t.site_id
  ORDER BY t.date_from IS NULL, t.date_from, t.created_at, t.rowid"""
REPORTS_SQL = """SELECT r.user_id, r.day, r.status, r.submitted_at, r.data_json
  FROM wf_reports r WHERE r.day BETWEEN ? AND ? ORDER BY r.day, r.user_id"""

ALL_SQL = (WORKERS_SQL, SITES_SQL, TASKS_SQL, REPORTS_SQL)


@contextmanager
def _ro_conn() -> Iterator[sqlite3.Connection | None]:
    path = get_settings().sqlite_path
    if not path.exists():
        yield None
        return
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


def _query(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    with _ro_conn() as conn:
        if conn is None:
            return []
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):  # the Firma module has not been initialised yet
                return []
            raise


def workers() -> list[WorkerRef]:
    return [WorkerRef(r["id"], r["name"], bool(r["active"])) for r in _query(WORKERS_SQL)]


def sites() -> list[SiteRef]:
    return [SiteRef(r["id"], r["name"], bool(r["active"])) for r in _query(SITES_SQL)]


def tasks() -> list[TaskFact]:
    return [
        TaskFact(
            id=r["id"],
            site_id=r["site_id"],
            site_name=r["site_name"],
            title=r["title"],
            description=r["description"] or "",
            status=r["status"],
            progress=r["progress"],
            date_from=r["date_from"],
            date_to=r["date_to"],
            done_on=r["done_on"],
            depends_on=r["depends_on"],
            assignees=tuple(json.loads(r["assignees"] or "[]")),
        )
        for r in _query(TASKS_SQL)
    ]


def reports(start: date, end: date) -> list[ReportFact]:
    out = []
    for r in _query(REPORTS_SQL, (start.isoformat(), end.isoformat())):
        out.append(
            ReportFact(
                user_id=r["user_id"],
                day=r["day"],
                status=r["status"],
                submitted_at=r["submitted_at"],
                data=ReportData.model_validate_json(r["data_json"] or "{}"),
            )
        )
    return out


@dataclass
class Snapshot:
    """Everything one answer needs — loaded at once (a company has tens to hundreds of
    tasks, not millions)."""

    workers: list[WorkerRef]
    sites: list[SiteRef]
    tasks: list[TaskFact]

    @cached_property
    def worker_by_id(self) -> dict[str, WorkerRef]:
        return {w.id: w for w in self.workers}

    @cached_property
    def task_by_id(self) -> dict[str, TaskFact]:
        return {t.id: t for t in self.tasks}


def snapshot() -> Snapshot:
    return Snapshot(workers=workers(), sites=sites(), tasks=tasks())

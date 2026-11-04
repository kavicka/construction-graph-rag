"""Tasks on a site and their assignees."""

import json
from typing import Any

from app.db import get_conn
from app.workforce.store.common import new_id

TASK_STATUSES = ("todo", "in_progress", "done", "cancelled")


TASK_FIELDS = (
    "site_id",
    "title",
    "description",
    "work_type",
    "date_from",
    "date_to",
    "depends_on",
    "status",
    "progress",
    "done_on",
    "source_id",
    "source_quote",
    "source_page",
)


def insert_task(fields: dict[str, Any], assignees: list[str]) -> str:
    task_id = new_id()
    cols = [k for k in TASK_FIELDS if k in fields]
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO wf_tasks (id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})",
            (task_id, *(fields[k] for k in cols)),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO wf_task_assignees (task_id, user_id) VALUES (?, ?)",
            [(task_id, u) for u in assignees],
        )
    return task_id


def update_task(task_id: str, fields: dict[str, Any]) -> None:
    cols = [k for k in TASK_FIELDS if k in fields]
    if not cols:
        return
    with get_conn() as conn:
        conn.execute(
            f"UPDATE wf_tasks SET {', '.join(f'{c} = ?' for c in cols)},"
            " updated_at = datetime('now') WHERE id = ?",
            (*(fields[k] for k in cols), task_id),
        )


def set_task_assignees(task_id: str, user_ids: list[str]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wf_task_assignees WHERE task_id = ?", (task_id,))
        conn.executemany(
            "INSERT INTO wf_task_assignees (task_id, user_id) VALUES (?, ?)",
            [(task_id, u) for u in dict.fromkeys(user_ids)],
        )


def delete_task(task_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wf_tasks WHERE id = ?", (task_id,))


TASK_SELECT = """SELECT t.*, s.name AS site_name,
  (SELECT json_group_array(a.user_id) FROM wf_task_assignees a WHERE a.task_id = t.id)
    AS assignees_json
  FROM wf_tasks t JOIN wf_sites s ON s.id = t.site_id"""


def _task(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["assignees"] = json.loads(d.pop("assignees_json") or "[]")
    return d


def get_task(task_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    return _task(row) if row else None


def list_tasks(
    *, site_id: str | None = None, user_id: str | None = None, source_ids: list[str] | None = None
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if site_id:
        clauses.append("t.site_id = ?")
        params.append(site_id)
    if user_id:
        clauses.append("t.id IN (SELECT task_id FROM wf_task_assignees WHERE user_id = ?)")
        params.append(user_id)
    if source_ids is not None:
        if not source_ids:
            return []
        clauses.append(f"t.source_id IN ({','.join('?' * len(source_ids))})")
        params += source_ids
    query = TASK_SELECT + (" WHERE " + " AND ".join(clauses) if clauses else "")
    with get_conn() as conn:
        rows = conn.execute(
            query + " ORDER BY t.date_from IS NULL, t.date_from, t.created_at, t.rowid", params
        ).fetchall()
    return [_task(r) for r in rows]

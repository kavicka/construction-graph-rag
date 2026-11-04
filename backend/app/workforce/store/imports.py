"""Imported task documents (sources, with version chains) and the task drafts
extracted from them for review."""

import json
from typing import Any

from app.db import get_conn
from app.workforce.store.common import new_id, rows_as_dicts


def insert_source(
    *, doc_id: str | None, site_id: str, filename: str, previous_id: str | None, version: int
) -> dict[str, Any]:
    source_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wf_task_sources (id, doc_id, site_id, filename, previous_id, version)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (source_id, doc_id, site_id, filename, previous_id, version),
        )
    src = get_source(source_id)
    assert src is not None
    return src


def get_source(source_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_task_sources WHERE id = ?", (source_id,)).fetchone()
    return dict(row) if row else None


def list_sources(site_id: str | None = None) -> list[dict[str, Any]]:
    query, params = "SELECT * FROM wf_task_sources", []
    if site_id:
        query += " WHERE site_id = ?"
        params.append(site_id)
    with get_conn() as conn:
        return rows_as_dicts(
            conn.execute(query + " ORDER BY created_at DESC, rowid DESC", params).fetchall()
        )


def set_source_status(
    source_id: str,
    status: str,
    *,
    progress: float | None = None,
    error: str | None = None,
    doc_date: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE wf_task_sources SET status = ?, progress = COALESCE(?, progress),
                 error = ?, doc_date = COALESCE(?, doc_date) WHERE id = ?""",
            (status, progress, error, doc_date, source_id),
        )


def source_chain(source_id: str) -> list[str]:
    """Id zdroja + všetkých jeho predošlých verzií (najnovšia prvá)."""
    chain: list[str] = []
    current: str | None = source_id
    while current and current not in chain:
        chain.append(current)
        src = get_source(current)
        current = src["previous_id"] if src else None
    return chain


def replace_drafts(source_id: str, drafts: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wf_task_drafts WHERE source_id = ?", (source_id,))
        conn.executemany(
            """INSERT INTO wf_task_drafts (id, source_id, kind, target_task_id, data_json,
                 decision, position) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    new_id(),
                    source_id,
                    d["kind"],
                    d.get("target_task_id"),
                    json.dumps(d["data"], ensure_ascii=False),
                    d.get("decision", "pending"),
                    i,
                )
                for i, d in enumerate(drafts)
            ],
        )


def _draft(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["data"] = json.loads(d.pop("data_json"))
    return d


def list_drafts(source_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wf_task_drafts WHERE source_id = ? ORDER BY position", (source_id,)
        ).fetchall()
    return [_draft(r) for r in rows]


def get_draft(draft_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_task_drafts WHERE id = ?", (draft_id,)).fetchone()
    return _draft(row) if row else None


def update_draft(draft_id: str, *, data: dict[str, Any], decision: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE wf_task_drafts SET data_json = ?, decision = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), decision, draft_id),
        )

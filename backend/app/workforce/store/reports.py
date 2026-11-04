"""Daily reports of workers: the report itself, its inputs (audio/text), follow-up
questions, and the owner's daily summary PDFs."""

import json
from typing import Any

from app.db import get_conn
from app.workforce.store.common import new_id, rows_as_dicts


def get_or_create_report(user_id: str, day: str) -> dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO wf_reports (id, user_id, day) VALUES (?, ?, ?)",
            (new_id(), user_id, day),
        )
        row = conn.execute(
            "SELECT * FROM wf_reports WHERE user_id = ? AND day = ?", (user_id, day)
        ).fetchone()
    return dict(row)


def get_report(report_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(row) if row else None


def list_reports(*, day: str | None = None, user_id: str | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if day:
        clauses.append("r.day = ?")
        params.append(day)
    if user_id:
        clauses.append("r.user_id = ?")
        params.append(user_id)
    query = "SELECT r.*, u.name AS worker_name FROM wf_reports r JOIN users u ON u.id = r.user_id"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with get_conn() as conn:
        return rows_as_dicts(
            conn.execute(query + " ORDER BY r.day DESC, u.name", params).fetchall()
        )


REPORT_FIELDS = (
    "status",
    "step",
    "error",
    "transcript",
    "data_json",
    "pdf_path",
    "pdf_pages",
    "submitted_at",
    "edited_by_owner",
)


def update_report(report_id: str, **fields: Any) -> None:
    cols = [k for k in REPORT_FIELDS if k in fields]
    if not cols:
        return
    with get_conn() as conn:
        conn.execute(
            f"UPDATE wf_reports SET {', '.join(f'{c} = ?' for c in cols)},"
            " updated_at = datetime('now') WHERE id = ?",
            (*(fields[k] for k in cols), report_id),
        )


def insert_input(
    report_id: str,
    kind: str,
    text: str,
    *,
    question_id: str | None = None,
    audio_path: str | None = None,
) -> str:
    input_id = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wf_report_inputs (id, report_id, question_id, kind, audio_path, text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (input_id, report_id, question_id, kind, audio_path, text),
        )
    return input_id


def set_input_text(input_id: str, text: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE wf_report_inputs SET text = ? WHERE id = ?", (text, input_id))


def list_inputs(report_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_as_dicts(
            conn.execute(
                "SELECT * FROM wf_report_inputs WHERE report_id = ? ORDER BY rowid", (report_id,)
            ).fetchall()
        )


def get_input(input_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_report_inputs WHERE id = ?", (input_id,)).fetchone()
    return dict(row) if row else None


def expired_audio_inputs(before_iso: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return rows_as_dicts(
            conn.execute(
                """SELECT * FROM wf_report_inputs WHERE audio_path IS NOT NULL
               AND created_at < ?""",
                (before_iso,),
            ).fetchall()
        )


def clear_input_audio(input_id: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE wf_report_inputs SET audio_path = NULL WHERE id = ?", (input_id,))


def list_questions(report_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
    query, params = "SELECT * FROM wf_questions WHERE report_id = ?", [report_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    with get_conn() as conn:
        return rows_as_dicts(conn.execute(query + " ORDER BY position, rowid", params).fetchall())


def get_question(question_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_questions WHERE id = ?", (question_id,)).fetchone()
    return dict(row) if row else None


def insert_question(
    report_id: str,
    *,
    key: str,
    kind: str,
    task_id: str | None,
    detail: str,
    text: str,
    position: int,
) -> str:
    qid = new_id()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wf_questions (id, report_id, key, kind, task_id, detail, text, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (qid, report_id, key, kind, task_id, detail, text, position),
        )
    return qid


QUESTION_FIELDS = ("detail", "text", "attempts", "mode", "status", "answer", "position")


def update_question(question_id: str, **fields: Any) -> None:
    cols = [k for k in QUESTION_FIELDS if k in fields]
    if not cols:
        return
    with get_conn() as conn:
        conn.execute(
            f"UPDATE wf_questions SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            (*(fields[k] for k in cols), question_id),
        )


def task_history(task_id: str) -> list[dict[str, Any]]:
    """Odovzdané výpisy, v ktorých sa úloha spomína (pre detail karty)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.id AS report_id, r.day, u.name AS worker_name, t.value AS entry_json
               FROM wf_reports r JOIN users u ON u.id = r.user_id,
                    json_each(r.data_json, '$.tasks') t
               WHERE r.status = 'submitted' AND json_extract(t.value, '$.task_id') = ?
               ORDER BY r.day DESC""",
            (task_id,),
        ).fetchall()
    return [
        {
            **{k: r[k] for k in ("report_id", "day", "worker_name")},
            "entry": json.loads(r["entry_json"]),
        }
        for r in rows
    ]


def get_daily_summary(day: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_daily_summaries WHERE day = ?", (day,)).fetchone()
    return dict(row) if row else None


def set_daily_summary(day: str, pdf_path: str, pages: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO wf_daily_summaries (day, pdf_path, pages) VALUES (?, ?, ?)
               ON CONFLICT(day) DO UPDATE SET pdf_path = excluded.pdf_path,
                 pages = excluded.pages, created_at = datetime('now')""",
            (day, pdf_path, pages),
        )

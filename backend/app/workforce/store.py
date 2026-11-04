"""SQLite vrstva modulu Firma — vlastná schéma (tabuľky wf_*), hlúpe CRUD funkcie.

Pracovník je bežný používateľ (users, rola member) + riadok vo wf_workers s PIN-om
a profesiami; prihlásenie a session zdieľa s hlavným auth.
"""

import json
import uuid
from typing import Any

from app.db import get_conn, revoke_credentials

SCHEMA = """
CREATE TABLE IF NOT EXISTS wf_workers (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  pin_hash TEXT NOT NULL,
  professions_json TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_professions (
  name TEXT PRIMARY KEY,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS wf_sites (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  address TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_task_sources (
  id TEXT PRIMARY KEY,
  doc_id TEXT,
  site_id TEXT NOT NULL REFERENCES wf_sites(id) ON DELETE CASCADE,
  filename TEXT NOT NULL,
  previous_id TEXT REFERENCES wf_task_sources(id) ON DELETE SET NULL,
  version INTEGER NOT NULL DEFAULT 1,
  doc_date TEXT,
  status TEXT NOT NULL DEFAULT 'waiting'
    CHECK (status IN ('waiting', 'extracting', 'review', 'applied', 'error')),
  progress REAL NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wf_tasks (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL REFERENCES wf_sites(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  work_type TEXT,
  date_from TEXT,
  date_to TEXT,
  depends_on TEXT REFERENCES wf_tasks(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'todo'
    CHECK (status IN ('todo', 'in_progress', 'done', 'cancelled')),
  progress INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  done_on TEXT,
  source_id TEXT REFERENCES wf_task_sources(id) ON DELETE SET NULL,
  source_quote TEXT,
  source_page INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_tasks_site ON wf_tasks(site_id);
CREATE INDEX IF NOT EXISTS idx_wf_tasks_source ON wf_tasks(source_id);
CREATE TABLE IF NOT EXISTS wf_task_assignees (
  task_id TEXT NOT NULL REFERENCES wf_tasks(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (task_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_wf_assignees_user ON wf_task_assignees(user_id);
CREATE TABLE IF NOT EXISTS wf_task_drafts (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES wf_task_sources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('new', 'changed', 'cancelled', 'unchanged')),
  target_task_id TEXT REFERENCES wf_tasks(id) ON DELETE SET NULL,
  data_json TEXT NOT NULL,
  decision TEXT NOT NULL DEFAULT 'pending'
    CHECK (decision IN ('pending', 'approved', 'rejected')),
  position INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_wf_drafts_source ON wf_task_drafts(source_id);
CREATE TABLE IF NOT EXISTS wf_reports (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft', 'processing', 'questioning', 'ready', 'submitted')),
  step TEXT,
  error TEXT,
  transcript TEXT NOT NULL DEFAULT '',
  data_json TEXT NOT NULL DEFAULT '{}',
  pdf_path TEXT,
  pdf_pages INTEGER,
  submitted_at TEXT,
  edited_by_owner INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, day)
);
CREATE INDEX IF NOT EXISTS idx_wf_reports_day ON wf_reports(day);
CREATE TABLE IF NOT EXISTS wf_report_inputs (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES wf_reports(id) ON DELETE CASCADE,
  question_id TEXT,
  kind TEXT NOT NULL CHECK (kind IN ('audio', 'text', 'choice')),
  audio_path TEXT,
  text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_inputs_report ON wf_report_inputs(report_id);
CREATE TABLE IF NOT EXISTS wf_questions (
  id TEXT PRIMARY KEY,
  report_id TEXT NOT NULL REFERENCES wf_reports(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  kind TEXT NOT NULL,
  task_id TEXT,
  detail TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  mode TEXT NOT NULL DEFAULT 'open' CHECK (mode IN ('open', 'choices')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'obsolete')),
  answer TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wf_questions_report ON wf_questions(report_id);
CREATE TABLE IF NOT EXISTS wf_daily_summaries (
  day TEXT PRIMARY KEY,
  pdf_path TEXT NOT NULL,
  pages INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_PROFESSIONS = [
    "murár",
    "tesár",
    "železiar",
    "betonár",
    "elektrikár",
    "inštalatér",
    "kúrenár",
    "obkladač",
    "maliar",
    "sadrokartonista",
    "pokrývač",
    "izolatér",
    "zvárač",
    "strojník",
    "pomocný robotník",
]

TASK_STATUSES = ("todo", "in_progress", "done", "cancelled")


def new_id() -> str:
    return uuid.uuid4().hex


def init_workforce_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM wf_professions").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO wf_professions (name, position) VALUES (?, ?)",
                [(p, i) for i, p in enumerate(DEFAULT_PROFESSIONS)],
            )


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# --- pracovníci -------------------------------------------------------------------------

WORKER_SELECT = """SELECT u.id, u.name, u.email, w.professions_json, w.active, w.pin_hash,
  w.failed_attempts, w.locked_until, w.created_at
  FROM wf_workers w JOIN users u ON u.id = w.user_id"""


def insert_worker(user_id: str, pin_hash: str, professions: list[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO wf_workers (user_id, pin_hash, professions_json) VALUES (?, ?, ?)",
            (user_id, pin_hash, json.dumps(professions, ensure_ascii=False)),
        )


def get_worker(user_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(WORKER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_workers(*, active_only: bool = False) -> list[dict[str, Any]]:
    query = WORKER_SELECT + (" WHERE w.active = 1" if active_only else "")
    with get_conn() as conn:
        return _rows(conn.execute(query + " ORDER BY u.name COLLATE NOCASE").fetchall())


def worker_ids() -> set[str]:
    with get_conn() as conn:
        return {r[0] for r in conn.execute("SELECT user_id FROM wf_workers").fetchall()}


def update_worker(user_id: str, *, name: str, professions: list[str], active: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        conn.execute(
            "UPDATE wf_workers SET professions_json = ?, active = ? WHERE user_id = ?",
            (json.dumps(professions, ensure_ascii=False), int(active), user_id),
        )
        if not active:
            # deaktivovaný pracovník sa odhlási všade
            revoke_credentials(user_id, conn)


def set_worker_pin(user_id: str, pin_hash: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE wf_workers SET pin_hash = ?, failed_attempts = 0, locked_until = NULL
               WHERE user_id = ?""",
            (pin_hash, user_id),
        )
        # a new PIN logs the worker out everywhere (lost or shared phone)
        revoke_credentials(user_id, conn)


def claim_pin_attempt(
    user_id: str, *, now_iso: str, max_attempts: int, lock_until_iso: str
) -> int | None:
    """Atomically count one PIN attempt BEFORE the PIN is verified.

    → attempt number in the current round, or None while the worker is locked. The
    attempt that reaches `max_attempts` sets the lock in the same statement, so parallel
    requests can never verify more than `max_attempts` guesses per lock period. An expired
    lock starts a fresh round."""
    with get_conn() as conn:
        row = conn.execute(
            """UPDATE wf_workers SET
                 failed_attempts = CASE WHEN locked_until IS NOT NULL THEN 1
                                        ELSE failed_attempts + 1 END,
                 locked_until = CASE WHEN (CASE WHEN locked_until IS NOT NULL THEN 1
                                                ELSE failed_attempts + 1 END) >= :max
                                     THEN :lock ELSE NULL END
               WHERE user_id = :uid AND (locked_until IS NULL OR locked_until <= :now)
               RETURNING failed_attempts""",
            {"uid": user_id, "now": now_iso, "max": max_attempts, "lock": lock_until_iso},
        ).fetchone()
    return int(row[0]) if row else None


def reset_pin_failures(user_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE wf_workers SET failed_attempts = 0, locked_until = NULL WHERE user_id = ?",
            (user_id,),
        )


def delete_worker(user_id: str) -> None:
    with get_conn() as conn:
        revoke_credentials(user_id, conn)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# --- profesie ---------------------------------------------------------------------------


def list_professions() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT name FROM wf_professions ORDER BY position, name").fetchall()
    return [r["name"] for r in rows]


def replace_professions(names: list[str]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wf_professions")
        conn.executemany(
            "INSERT INTO wf_professions (name, position) VALUES (?, ?)",
            [(n, i) for i, n in enumerate(names)],
        )


# --- stavby -----------------------------------------------------------------------------


def insert_site(name: str, address: str) -> dict[str, Any]:
    site_id = new_id()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO wf_sites (id, name, address) VALUES (?, ?, ?)", (site_id, name, address)
        )
    site = get_site(site_id)
    assert site is not None
    return site


def get_site(site_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM wf_sites WHERE id = ?", (site_id,)).fetchone()
    return dict(row) if row else None


def list_sites(*, active_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM wf_sites" + (" WHERE active = 1" if active_only else "")
    with get_conn() as conn:
        return _rows(conn.execute(query + " ORDER BY name COLLATE NOCASE").fetchall())


def update_site(site_id: str, *, name: str, address: str, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE wf_sites SET name = ?, address = ?, active = ? WHERE id = ?",
            (name, address, int(active), site_id),
        )


def delete_site(site_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wf_sites WHERE id = ?", (site_id,))


# --- úlohy ------------------------------------------------------------------------------

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


# --- zdroje úloh (importované dokumenty) a návrhy ---------------------------------------


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
        return _rows(
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


# --- denné výpisy -----------------------------------------------------------------------


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
        return _rows(conn.execute(query + " ORDER BY r.day DESC, u.name", params).fetchall())


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
        return _rows(
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
        return _rows(
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
        return _rows(conn.execute(query + " ORDER BY position, rowid", params).fetchall())


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

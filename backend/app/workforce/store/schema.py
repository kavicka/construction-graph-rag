"""Schema of the Firma module (wf_* tables) and its seed data."""

from app.db import get_conn

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


def init_workforce_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM wf_professions").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO wf_professions (name, position) VALUES (?, ?)",
                [(p, i) for i, p in enumerate(DEFAULT_PROFESSIONS)],
            )

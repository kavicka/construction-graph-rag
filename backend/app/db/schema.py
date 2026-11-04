"""Schema of the main database and the additive column migrations."""

from app.db.connection import get_conn
from app.security import get_security_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  filename TEXT NOT NULL,
  sha256 TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'queued',
  stage_progress REAL NOT NULL DEFAULT 0,
  pages_count INTEGER,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS pages (
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  text_len INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (doc_id, page_no)
);
CREATE TABLE IF NOT EXISTS tables_meta (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER,
  caption TEXT,
  n_rows INTEGER NOT NULL,
  n_cols INTEGER NOT NULL,
  markdown_path TEXT NOT NULL,
  parquet_path TEXT,
  summary TEXT
);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  sources_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS records (
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  table_id TEXT NOT NULL,
  page_no INTEGER,
  category TEXT,
  name TEXT NOT NULL,
  attrs_json TEXT NOT NULL,
  raw_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_doc ON records(doc_id);
CREATE INDEX IF NOT EXISTS idx_records_category ON records(category);
CREATE TABLE IF NOT EXISTS record_attrs (
  doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  label TEXT NOT NULL,
  unit TEXT,
  PRIMARY KEY (doc_id, key)
);
CREATE TABLE IF NOT EXISTS graph_nodes (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  label TEXT NOT NULL,
  norm_key TEXT,
  doc_id TEXT,
  props_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_norm ON graph_nodes(norm_key);
CREATE INDEX IF NOT EXISTS idx_nodes_doc ON graph_nodes(doc_id);
CREATE TABLE IF NOT EXISTS graph_edges (
  src TEXT NOT NULL,
  dst TEXT NOT NULL,
  type TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  evidence_chunk_id TEXT NOT NULL DEFAULT '',
  props_json TEXT,
  PRIMARY KEY (src, dst, type, evidence_chunk_id)
);
CREATE INDEX IF NOT EXISTS idx_edges_src ON graph_edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON graph_edges(dst);
CREATE TABLE IF NOT EXISTS company_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data_json TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS trace_spans (
  trace_id TEXT NOT NULL,
  span_id TEXT PRIMARY KEY,
  parent_id TEXT,
  name TEXT NOT NULL,
  start_ns INTEGER NOT NULL,
  end_ns INTEGER NOT NULL,
  status TEXT NOT NULL,
  attrs_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_root ON trace_spans(parent_id, start_ns);
CREATE TABLE IF NOT EXISTS feedback (
  message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
  rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
  comment TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunk_boxes (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  boxes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_boxes_doc ON chunk_boxes(doc_id);
CREATE TABLE IF NOT EXISTS community_summaries (
  key TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
  groups_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chunk_contexts (
  key TEXT PRIMARY KEY,
  context TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generated_documents (
  id TEXT PRIMARY KEY,
  chat_id TEXT REFERENCES chats(id) ON DELETE SET NULL,
  title TEXT NOT NULL,
  doc_type TEXT NOT NULL,
  pages INTEGER NOT NULL,
  sources_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# stĺpce pridané po prvom release — CREATE TABLE IF NOT EXISTS ich do starej DB nedostane
MIGRATIONS: list[tuple[str, str, str]] = [
    ("messages", "document_json", "TEXT"),
    ("messages", "trace_id", "TEXT"),
    ("messages", "verify_json", "TEXT"),
    ("tables_meta", "boxes_json", "TEXT"),
    ("documents", "format", "TEXT NOT NULL DEFAULT 'pdf'"),
    ("documents", "source_path", "TEXT"),
    ("documents", "access_json", "TEXT NOT NULL DEFAULT '[\"*\"]'"),
    ("chats", "user_id", "TEXT"),
    ("generated_documents", "user_id", "TEXT"),
    ("api_tokens", "id", "TEXT"),
    ("api_tokens", "expires_at", "TEXT"),
]


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        # API tokens minted before ids/expiry existed: give them an id and the default TTL
        conn.execute("UPDATE api_tokens SET id = lower(hex(randomblob(16))) WHERE id IS NULL")
        conn.execute(
            """UPDATE api_tokens SET expires_at = strftime('%Y-%m-%dT%H:%M:%S+00:00',
                 created_at, ?) WHERE expires_at IS NULL""",
            (f"+{get_security_settings().api_token_ttl_days} days",),
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS api_tokens_id ON api_tokens(id)")

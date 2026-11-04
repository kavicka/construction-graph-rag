"""SQLite vrstva — synchrónna, volaná cez run_in_threadpool z async handlerov."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.config import get_settings

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
]

# status pipeline: queued → parsing → tables → embedding → graph → done | error
INGEST_STAGES = [
    "queued",
    "parsing",
    "tables",
    "context",
    "embedding",
    "visual",
    "graph",
    "done",
]


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.sqlite_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def insert_document(
    doc_id: str, filename: str, sha256: str, *, fmt: str = "pdf", source_path: str | None = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, filename, sha256, format, source_path)"
            " VALUES (?, ?, ?, ?, ?)",
            (doc_id, filename, sha256, fmt, source_path),
        )


def delete_document(doc_id: str) -> None:
    """Riadok dokumentu + všetko, čo naň odkazuje (pages, tables_meta, records cez CASCADE)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM chunk_boxes WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def find_document_by_sha(sha256: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
    return dict(row) if row else None


def get_document(doc_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_documents() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def set_document_status(
    doc_id: str, status: str, *, progress: float = 0.0, error: str | None = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE documents SET status = ?, stage_progress = ?, error = ? WHERE id = ?",
            (status, progress, error, doc_id),
        )


def set_pages(doc_id: str, pages: list[tuple[int, int]]) -> None:
    """pages: zoznam (page_no, text_len)."""
    with get_conn() as conn:
        conn.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO pages (doc_id, page_no, text_len) VALUES (?, ?, ?)",
            [(doc_id, no, ln) for no, ln in pages],
        )
        conn.execute("UPDATE documents SET pages_count = ? WHERE id = ?", (len(pages), doc_id))


def get_pages(doc_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT page_no, text_len FROM pages WHERE doc_id = ? ORDER BY page_no", (doc_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def insert_table(
    table_id: str,
    doc_id: str,
    page_no: int | None,
    caption: str | None,
    n_rows: int,
    n_cols: int,
    markdown_path: str,
    parquet_path: str | None,
    boxes_json: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tables_meta (id, doc_id, page_no, caption, n_rows, n_cols,
               markdown_path, parquet_path, boxes_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                table_id,
                doc_id,
                page_no,
                caption,
                n_rows,
                n_cols,
                markdown_path,
                parquet_path,
                boxes_json,
            ),
        )


def list_tables(doc_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tables_meta WHERE doc_id = ? ORDER BY page_no", (doc_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_table(table_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tables_meta WHERE id = ?", (table_id,)).fetchone()
    return dict(row) if row else None


def set_table_summary(table_id: str, summary: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE tables_meta SET summary = ? WHERE id = ?", (summary, table_id))


def delete_document_tables(doc_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tables_meta WHERE doc_id = ?", (doc_id,))


def replace_document_records(doc_id: str, rows: list[dict[str, Any]]) -> int:
    with get_conn() as conn:
        conn.execute("DELETE FROM records WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            """INSERT INTO records (id, doc_id, table_id, page_no, category, name,
               attrs_json, raw_text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r["id"],
                    doc_id,
                    r["table_id"],
                    r["page_no"],
                    r["category"],
                    r["name"],
                    r["attrs_json"],
                    r["raw_text"],
                )
                for r in rows
            ],
        )
    return len(rows)


def replace_record_attrs(doc_id: str, attrs: list[tuple[str, str, str | None]]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM record_attrs WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO record_attrs (doc_id, key, label, unit) VALUES (?, ?, ?, ?)",
            [(doc_id, k, label, unit) for k, label, unit in attrs],
        )


def list_record_attrs() -> list[dict[str, Any]]:
    """Register atribútov naprieč knižnicou (kľúč → štítok, jednotka, počet dokumentov)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT key, MIN(label) AS label, MIN(unit) AS unit, COUNT(*) AS docs
               FROM record_attrs GROUP BY key ORDER BY key"""
        ).fetchall()
    return [dict(r) for r in rows]


def list_records(doc_id: str | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM records"
    params: list[Any] = []
    if doc_id:
        query += " WHERE doc_id = ?"
        params.append(doc_id)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def upsert_graph_node(
    node_id: str,
    node_type: str,
    label: str,
    norm_key: str | None = None,
    doc_id: str | None = None,
    props_json: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO graph_nodes (id, type, label, norm_key, doc_id, props_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET label=excluded.label,
                 props_json=COALESCE(excluded.props_json, graph_nodes.props_json)""",
            (node_id, node_type, label, norm_key, doc_id, props_json),
        )


def upsert_graph_edge(
    src: str,
    dst: str,
    edge_type: str,
    *,
    weight: float = 1.0,
    evidence_chunk_id: str = "",
    props_json: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO graph_edges (src, dst, type, weight, evidence_chunk_id, props_json)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(src, dst, type, evidence_chunk_id)
               DO UPDATE SET weight=excluded.weight""",
            (src, dst, edge_type, weight, evidence_chunk_id, props_json),
        )


def delete_document_graph(doc_id: str) -> None:
    """Zmaž doc-viazané uzly + ich hrany + osirotené zdieľané uzly."""
    with get_conn() as conn:
        doc_nodes = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM graph_nodes WHERE doc_id = ?", (doc_id,)
            ).fetchall()
        ]
        if doc_nodes:
            placeholders = ",".join("?" * len(doc_nodes))
            conn.execute(
                f"DELETE FROM graph_edges WHERE src IN ({placeholders}) OR dst IN ({placeholders})",
                doc_nodes + doc_nodes,
            )
            conn.execute(f"DELETE FROM graph_nodes WHERE id IN ({placeholders})", doc_nodes)
        # zdieľané uzly bez akejkoľvek hrany už nič nedokladá — preč
        conn.execute(
            """DELETE FROM graph_nodes WHERE doc_id IS NULL AND id NOT IN
               (SELECT src FROM graph_edges UNION SELECT dst FROM graph_edges)"""
        )


def list_graph_nodes(
    types: list[str] | None = None, doc_id: str | None = None
) -> list[dict[str, Any]]:
    query = "SELECT * FROM graph_nodes"
    clauses: list[str] = []
    params: list[Any] = []
    if types:
        clauses.append(f"type IN ({','.join('?' * len(types))})")
        params += types
    if doc_id:
        clauses.append("(doc_id = ? OR doc_id IS NULL)")
        params.append(doc_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def list_graph_edges() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM graph_edges").fetchall()
    return [dict(r) for r in rows]


def get_graph_node(node_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM graph_nodes WHERE id = ?", (node_id,)).fetchone()
    return dict(row) if row else None


def node_edges(node_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM graph_edges WHERE src = ? OR dst = ?", (node_id, node_id)
        ).fetchall()
    return [dict(r) for r in rows]


def concept_nodes() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM graph_nodes WHERE type IN ('concept', 'reference')"
        ).fetchall()
    return [dict(r) for r in rows]


def create_chat(chat_id: str, title: str, user_id: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chats (id, title, user_id) VALUES (?, ?, ?)", (chat_id, title, user_id)
        )


def get_chat(chat_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def list_chats(user_id: str | None = None) -> list[dict[str, Any]]:
    """user_id=None → všetky (bez prihlasovania / admin); inak len chaty používateľa."""
    query, params = "SELECT * FROM chats", []
    if user_id is not None:
        query += " WHERE user_id = ?"
        params.append(user_id)
    with get_conn() as conn:
        rows = conn.execute(query + " ORDER BY created_at DESC", params).fetchall()
    return [dict(r) for r in rows]


def add_message(
    message_id: str,
    chat_id: str,
    role: str,
    content: str,
    sources_json: str | None = None,
    document_json: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, sources_json, document_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, chat_id, role, content, sources_json, document_json),
        )


def insert_spans(rows: list[tuple[Any, ...]]) -> None:
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO trace_spans
               (trace_id, span_id, parent_id, name, start_ns, end_ns, status, attrs_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )


TRACE_ROOTS = ("rag.query", "rag.ingest")


def list_root_spans(limit: int = 50) -> list[dict[str, Any]]:
    """Posledné traces (root spany) + súčty tokenov a počty spanov za celý trace."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT r.*,
                 (SELECT COUNT(*) FROM trace_spans s WHERE s.trace_id = r.trace_id) AS n_spans,
                 (SELECT COALESCE(SUM(json_extract(s.attrs_json,
                    '$."gen_ai.usage.input_tokens"')), 0)
                  FROM trace_spans s WHERE s.trace_id = r.trace_id) AS input_tokens,
                 (SELECT COALESCE(SUM(json_extract(s.attrs_json,
                    '$."gen_ai.usage.output_tokens"')), 0)
                  FROM trace_spans s WHERE s.trace_id = r.trace_id) AS output_tokens,
                 (SELECT f.rating FROM messages m JOIN feedback f ON f.message_id = m.id
                  WHERE m.trace_id = r.trace_id) AS rating
               FROM trace_spans r
               WHERE r.parent_id IS NULL AND r.name IN (?, ?)
               ORDER BY r.start_ns DESC LIMIT ?""",
            (*TRACE_ROOTS, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_trace_spans(trace_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trace_spans WHERE trace_id = ? ORDER BY start_ns", (trace_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_message_meta(message_id: str, trace_id: str | None, verify_json: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE messages SET trace_id = ?, verify_json = ? WHERE id = ?",
            (trace_id, verify_json, message_id),
        )


def preceding_question(message_id: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.content FROM messages m JOIN messages u ON u.chat_id = m.chat_id
               WHERE m.id = ? AND u.role = 'user' AND u.rowid < m.rowid
               ORDER BY u.rowid DESC LIMIT 1""",
            (message_id,),
        ).fetchone()
    return str(row["content"]) if row else None


def set_message_document(message_id: str, document_json: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE messages SET document_json = ? WHERE id = ?", (document_json, message_id)
        )


def get_message(message_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def set_feedback(message_id: str, rating: int, comment: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO feedback (message_id, rating, comment) VALUES (?, ?, ?)
               ON CONFLICT(message_id) DO UPDATE SET rating = excluded.rating,
                 comment = excluded.comment, created_at = datetime('now')""",
            (message_id, rating, comment),
        )


def message_ratings(chat_id: str) -> dict[str, int]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT f.message_id, f.rating FROM feedback f
               JOIN messages m ON m.id = f.message_id WHERE m.chat_id = ?""",
            (chat_id,),
        ).fetchall()
    return {r["message_id"]: r["rating"] for r in rows}


def delete_feedback(message_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM feedback WHERE message_id = ?", (message_id,))


def list_feedback(rating: int | None = None) -> list[dict[str, Any]]:
    """Feedback s otázkou (predchádzajúca user správa) a odpoveďou — export do golden setu."""
    query = """SELECT f.message_id, f.rating, f.comment, f.created_at, m.content AS answer,
                 m.sources_json, m.trace_id, m.chat_id,
                 (SELECT u.content FROM messages u WHERE u.chat_id = m.chat_id
                    AND u.role = 'user' AND u.rowid < m.rowid
                  ORDER BY u.rowid DESC LIMIT 1) AS question
               FROM feedback f JOIN messages m ON m.id = f.message_id"""
    params: list[Any] = []
    if rating is not None:
        query += " WHERE f.rating = ?"
        params.append(rating)
    query += " ORDER BY f.created_at DESC"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def replace_chunk_boxes(doc_id: str, boxes: dict[str, list[list[float]]]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM chunk_boxes WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO chunk_boxes (chunk_id, doc_id, boxes_json) VALUES (?, ?, ?)",
            [(cid, doc_id, json.dumps(b)) for cid, b in boxes.items() if b],
        )


def get_chunk_boxes(chunk_ids: list[str]) -> dict[str, list[list[float]]]:
    if not chunk_ids:
        return {}
    placeholders = ",".join("?" * len(chunk_ids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT chunk_id, boxes_json FROM chunk_boxes WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
    return {r["chunk_id"]: json.loads(r["boxes_json"]) for r in rows}


def get_community_summary(key: str) -> tuple[str, str] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT title, summary FROM community_summaries WHERE key = ?", (key,)
        ).fetchone()
    return (row["title"], row["summary"]) if row else None


def set_community_summary(key: str, title: str, summary: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO community_summaries (key, title, summary) VALUES (?, ?, ?)",
            (key, title, summary),
        )


def insert_user(
    user_id: str, email: str, name: str, password_hash: str, role: str, groups_json: str
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users (id, email, name, password_hash, role, groups_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, email, name, password_hash, role, groups_json),
        )


def get_user(user_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().casefold(),)
        ).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def count_users() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


def update_user(user_id: str, *, role: str, groups_json: str, name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET role = ?, groups_json = ?, name = ? WHERE id = ?",
            (role, groups_json, name, user_id),
        )


def update_user_password(user_id: str, password_hash: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        # po resete hesla musia staré prihlásenia aj tokeny prestať platiť
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))


def delete_user(user_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def insert_session(token_hash: str, user_id: str, expires_at: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, user_id, expires_at),
        )


def user_for_session(token_hash: str, now_iso: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (token_hash, now_iso),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token_hash: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def insert_api_token(token_hash: str, user_id: str, name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_tokens (token_hash, user_id, name) VALUES (?, ?, ?)",
            (token_hash, user_id, name),
        )


def user_for_api_token(token_hash: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM api_tokens t JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ?""",
            (token_hash,),
        ).fetchone()
    return dict(row) if row else None


def set_document_access(doc_id: str, access_json: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE documents SET access_json = ? WHERE id = ?", (access_json, doc_id))


def get_chunk_context(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT context FROM chunk_contexts WHERE key = ?", (key,)).fetchone()
    return str(row["context"]) if row else None


def set_chunk_context(key: str, context: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO chunk_contexts (key, context) VALUES (?, ?)", (key, context)
        )


def insert_generated_document(
    doc_id: str,
    chat_id: str | None,
    title: str,
    doc_type: str,
    pages: int,
    sources_json: str | None,
    user_id: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO generated_documents
               (id, chat_id, title, doc_type, pages, sources_json, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (doc_id, chat_id, title, doc_type, pages, sources_json, user_id),
        )


def get_generated_document(doc_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM generated_documents WHERE id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def list_generated_documents() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM generated_documents ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def list_messages(chat_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY rowid", (chat_id,)
        ).fetchall()
    return [dict(r) for r in rows]

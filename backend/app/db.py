"""SQLite vrstva — synchrónna, volaná cez run_in_threadpool z async handlerov."""

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
"""

# status pipeline: queued → parsing → tables → embedding → graph → done | error
INGEST_STAGES = ["queued", "parsing", "tables", "embedding", "graph", "done"]


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


def insert_document(doc_id: str, filename: str, sha256: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO documents (id, filename, sha256) VALUES (?, ?, ?)",
            (doc_id, filename, sha256),
        )


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
        conn.execute(
            "UPDATE documents SET pages_count = ? WHERE id = ?", (len(pages), doc_id)
        )


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
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tables_meta
               (id, doc_id, page_no, caption, n_rows, n_cols, markdown_path, parquet_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (table_id, doc_id, page_no, caption, n_rows, n_cols, markdown_path, parquet_path),
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
                f"DELETE FROM graph_edges WHERE src IN ({placeholders})"
                f" OR dst IN ({placeholders})",
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


def create_chat(chat_id: str, title: str) -> None:
    with get_conn() as conn:
        conn.execute("INSERT INTO chats (id, title) VALUES (?, ?)", (chat_id, title))


def get_chat(chat_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (chat_id,)).fetchone()
    return dict(row) if row else None


def list_chats() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM chats ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def add_message(
    message_id: str, chat_id: str, role: str, content: str, sources_json: str | None = None
) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, sources_json)"
            " VALUES (?, ?, ?, ?, ?)",
            (message_id, chat_id, role, content, sources_json),
        )


def list_messages(chat_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY rowid", (chat_id,)
        ).fetchall()
    return [dict(r) for r in rows]

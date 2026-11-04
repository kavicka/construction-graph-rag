"""Library documents: ingest status, pages, access groups and per-chunk artefacts."""

import json
from collections.abc import Iterable
from typing import Any

from app.db.connection import batched, get_conn

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
    """The document row plus everything referencing it (pages, tables_meta, records via CASCADE)."""
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


def get_documents(doc_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Batch lookup: document id → row; unknown ids are absent from the result."""
    out: dict[str, dict[str, Any]] = {}
    with get_conn() as conn:
        for batch in batched(doc_ids):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT * FROM documents WHERE id IN ({placeholders})", batch
            ).fetchall()
            out.update((r["id"], dict(r)) for r in rows)
    return out


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
    """pages: list of (page_no, text_len)."""
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

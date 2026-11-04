"""Metadata of PDF documents generated from chat answers (app.docgen)."""

from typing import Any

from app.db.connection import get_conn


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

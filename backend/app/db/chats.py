"""Chats, their messages and the per-answer feedback (thumbs up/down)."""

from typing import Any

from app.db.connection import get_conn


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
    """user_id=None → all chats (login off / admin); otherwise only the user's chats."""
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
    """Feedback with its question (the preceding user message) and answer — golden-set export."""
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


def list_messages(chat_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id = ? ORDER BY rowid", (chat_id,)
        ).fetchall()
    return [dict(r) for r in rows]

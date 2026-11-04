"""Users, login sessions and API tokens (only token hashes are stored)."""

import sqlite3
from typing import Any

from app.db.connection import get_conn


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
        # after a password reset, old logins and tokens must stop being valid
        revoke_credentials(user_id, conn)


def delete_user(user_id: str) -> None:
    with get_conn() as conn:
        revoke_credentials(user_id, conn)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def revoke_credentials(user_id: str, conn: sqlite3.Connection | None = None) -> None:
    """Delete all sessions and API tokens of a user (password/PIN reset, deactivation,
    deletion). Pass `conn` to do it in the caller's transaction."""
    if conn is None:
        with get_conn() as own:
            revoke_credentials(user_id, own)
        return
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user_id,))


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


def insert_api_token(
    token_hash: str, user_id: str, name: str, token_id: str, expires_at: str
) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO api_tokens (token_hash, user_id, name, id, expires_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token_hash, user_id, name, token_id, expires_at),
        )


def user_for_api_token(token_hash: str, now_iso: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM api_tokens t JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.expires_at > ?""",
            (token_hash, now_iso),
        ).fetchone()
    return dict(row) if row else None


API_TOKEN_COLUMNS = "t.id, t.user_id, u.email AS user_email, t.name, t.created_at, t.expires_at"


def get_api_token(token_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT {API_TOKEN_COLUMNS} FROM api_tokens t JOIN users u ON u.id = t.user_id
                WHERE t.id = ?""",
            (token_id,),
        ).fetchone()
    return dict(row) if row else None


def list_api_tokens(user_id: str | None = None) -> list[dict[str, Any]]:
    """Token metadata (never the hash); user_id=None → all users."""
    query = f"SELECT {API_TOKEN_COLUMNS} FROM api_tokens t JOIN users u ON u.id = t.user_id"
    params: tuple[str, ...] = ()
    if user_id is not None:
        query += " WHERE t.user_id = ?"
        params = (user_id,)
    with get_conn() as conn:
        rows = conn.execute(query + " ORDER BY t.created_at, t.id", params).fetchall()
    return [dict(r) for r in rows]


def delete_api_token(token_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))

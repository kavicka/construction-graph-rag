"""Workers (a `users` row + a `wf_workers` row with PIN and professions) and the
profession list."""

import json
from typing import Any

from app.db import get_conn, revoke_credentials
from app.workforce.store.common import rows_as_dicts

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
        return rows_as_dicts(conn.execute(query + " ORDER BY u.name COLLATE NOCASE").fetchall())


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
            # a deactivated worker is logged out everywhere
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

"""Construction sites."""

from typing import Any

from app.db import get_conn
from app.workforce.store.common import new_id, rows_as_dicts


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
        return rows_as_dicts(conn.execute(query + " ORDER BY name COLLATE NOCASE").fetchall())


def update_site(site_id: str, *, name: str, address: str, active: bool) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE wf_sites SET name = ?, address = ?, active = ? WHERE id = ?",
            (name, address, int(active), site_id),
        )


def delete_site(site_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM wf_sites WHERE id = ?", (site_id,))

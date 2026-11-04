"""Extracted tables and the structured records (rows with numeric attributes) behind them."""

from typing import Any

from app.db.connection import get_conn


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


def list_record_categories(doc_ids: set[str] | None = None) -> list[str]:
    """Sorted distinct non-empty record categories; `doc_ids` limits them to those
    documents (None = every document, e.g. for an unrestricted access scope)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT doc_id, category FROM records"
            " WHERE category IS NOT NULL AND category != ''"
        ).fetchall()
    return sorted({r["category"] for r in rows if doc_ids is None or r["doc_id"] in doc_ids})

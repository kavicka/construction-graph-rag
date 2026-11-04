"""OpenTelemetry spans persisted locally for the traces UI."""

from typing import Any

from app.db.connection import get_conn


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

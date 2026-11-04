"""Knowledge graph nodes and edges, plus cached community summaries."""

from collections.abc import Iterable
from typing import Any

from app.db.connection import batched, get_conn


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
    """Delete document-bound nodes + their edges + orphaned shared nodes."""
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
        # shared nodes with no edge left are backed by nothing — remove them
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


def get_graph_nodes(node_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Batch lookup: node id → row; unknown ids are absent from the result."""
    out: dict[str, dict[str, Any]] = {}
    with get_conn() as conn:
        for batch in batched(node_ids):
            placeholders = ",".join("?" * len(batch))
            rows = conn.execute(
                f"SELECT * FROM graph_nodes WHERE id IN ({placeholders})", batch
            ).fetchall()
            out.update((r["id"], dict(r)) for r in rows)
    return out


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

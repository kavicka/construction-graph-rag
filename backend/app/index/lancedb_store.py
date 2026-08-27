"""LanceDB — embedded vektorová DB (dáta = súbory na disku, žiadny server).

Jedna tabuľka `chunks`: dense vektor + text s natívnym FTS (BM25) indexom.
"""

import logging
from functools import lru_cache
from typing import Any

import pyarrow as pa

from app.config import get_settings
from app.index.chunker import Chunk

logger = logging.getLogger(__name__)

CHUNKS_TABLE = "chunks"


@lru_cache
def _connect() -> Any:
    import lancedb

    settings = get_settings()
    settings.lancedb_dir.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(settings.lancedb_dir)


def _schema(dim: int) -> pa.Schema:
    return pa.schema(
        [
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("chunk_id", pa.string()),
            pa.field("doc_id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("raw_text", pa.string()),
            pa.field("page_from", pa.int32()),
            pa.field("page_to", pa.int32()),
            pa.field("section_path", pa.string()),
            pa.field("type", pa.string()),
            pa.field("table_id", pa.string()),
        ]
    )


def _get_or_create_table(dim: int) -> Any:
    db = _connect()
    if CHUNKS_TABLE in db.table_names():
        return db.open_table(CHUNKS_TABLE)
    return db.create_table(CHUNKS_TABLE, schema=_schema(dim))


def replace_document_chunks(
    doc_id: str, chunks: list[Chunk], vectors: list[list[float]]
) -> int:
    """Idempotentné — pri re-ingeste sa staré chunky dokumentu zmažú."""
    assert len(chunks) == len(vectors)
    if not vectors:
        return 0
    tbl = _get_or_create_table(dim=len(vectors[0]))
    tbl.delete(f"doc_id = '{doc_id}'")
    tbl.add(
        [
            {
                "vector": vec,
                "chunk_id": c.chunk_id,
                "doc_id": c.doc_id,
                "text": c.text,
                "raw_text": c.raw_text,
                "page_from": c.page_from,
                "page_to": c.page_to,
                "section_path": c.section_path,
                "type": c.type,
                "table_id": c.table_id or "",
            }
            for c, vec in zip(chunks, vectors, strict=True)
        ]
    )
    # FTS index prebudujeme po každom ingeste — pri stovkách dokumentov sekundy
    tbl.create_fts_index("text", replace=True)
    return len(chunks)


def _row_out(row: dict[str, Any], score_key: str) -> dict[str, Any]:
    return {
        "chunk_id": row["chunk_id"],
        "doc_id": row["doc_id"],
        "text": row["text"],
        "raw_text": row["raw_text"],
        "page_from": row["page_from"],
        "page_to": row["page_to"],
        "section_path": row["section_path"],
        "type": row["type"],
        "table_id": row["table_id"] or None,
        "score": row.get(score_key),
    }


def _table_or_none() -> Any | None:
    db = _connect()
    if CHUNKS_TABLE not in db.table_names():
        return None
    return db.open_table(CHUNKS_TABLE)


def search_dense(vector: list[float], k: int = 24) -> list[dict[str, Any]]:
    tbl = _table_or_none()
    if tbl is None:
        return []
    rows = tbl.search(vector).limit(k).to_list()
    return [_row_out(r, "_distance") for r in rows]


def search_fts(query: str, k: int = 24) -> list[dict[str, Any]]:
    tbl = _table_or_none()
    if tbl is None:
        return []
    try:
        rows = tbl.search(query, query_type="fts").limit(k).to_list()
    except Exception:
        logger.exception("FTS vyhľadávanie zlyhalo (index ešte nemusí existovať)")
        return []
    return [_row_out(r, "_score") for r in rows]


def get_chunks_by_ids(chunk_ids: list[str]) -> list[dict[str, Any]]:
    tbl = _table_or_none()
    if tbl is None or not chunk_ids:
        return []
    quoted = ",".join("'" + cid.replace("'", "''") + "'" for cid in chunk_ids)
    rows = tbl.search().where(f"chunk_id IN ({quoted})").limit(len(chunk_ids)).to_list()
    return [_row_out(r, "_distance") for r in rows]


def document_chunks(doc_id: str) -> list[dict[str, Any]]:
    """Všetky chunky dokumentu v poradí vzniku (index z chunk_id suffixu)."""
    tbl = _table_or_none()
    if tbl is None:
        return []
    rows = tbl.search().where(f"doc_id = '{doc_id}'").limit(100_000).to_list()

    def order(row: dict[str, Any]) -> tuple[int, int]:
        suffix = row["chunk_id"].rsplit(":", 1)[-1]
        num = int(suffix[1:]) if suffix.startswith("t") and suffix[1:].isdigit() else 10**9
        return (row["page_from"], num)

    return sorted((_row_out(r, "_distance") for r in rows), key=order)


def count_chunks(doc_id: str | None = None) -> int:
    tbl = _table_or_none()
    if tbl is None:
        return 0
    if doc_id is None:
        return int(tbl.count_rows())
    return int(tbl.count_rows(f"doc_id = '{doc_id}'"))

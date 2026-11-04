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


def replace_document_chunks(doc_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> int:
    """Idempotentné — pri re-ingeste sa staré chunky dokumentu zmažú."""
    assert len(chunks) == len(vectors)
    if not vectors:
        return 0
    tbl = _get_or_create_table(dim=len(vectors[0]))
    tbl.delete(f"doc_id = {_sql_str(doc_id)}")
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


def delete_document_chunks(doc_id: str) -> None:
    tbl = _table_or_none()
    if tbl is None:
        return
    tbl.delete(f"doc_id = {_sql_str(doc_id)}")
    if tbl.count_rows():
        tbl.create_fts_index("text", replace=True)


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


def _sql_str(value: str) -> str:
    """Quoted SQL string literal for LanceDB filters (ids may come from the outside)."""
    return "'" + value.replace("'", "''") + "'"


def _doc_filter(doc_ids: set[str]) -> str:
    quoted = ",".join(_sql_str(d) for d in sorted(doc_ids))
    return f"doc_id IN ({quoted})"


def search_dense(
    vector: list[float], k: int = 24, doc_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """doc_ids = prístupové obmedzenie — filtruje sa vo vektorovom dotaze (prefilter),
    takže top-k je vždy z povolených dokumentov, nie orezaný výsledok."""
    tbl = _table_or_none()
    if tbl is None or doc_ids == set():
        return []
    q = tbl.search(vector)
    if doc_ids is not None:
        q = q.where(_doc_filter(doc_ids), prefilter=True)
    rows = q.limit(k).to_list()
    return [_row_out(r, "_distance") for r in rows]


def search_fts(query: str, k: int = 24, doc_ids: set[str] | None = None) -> list[dict[str, Any]]:
    tbl = _table_or_none()
    if tbl is None or doc_ids == set():
        return []
    try:
        q = tbl.search(query, query_type="fts")
        if doc_ids is not None:
            q = q.where(_doc_filter(doc_ids), prefilter=True)
        rows = q.limit(k).to_list()
    except Exception:
        logger.exception("FTS vyhľadávanie zlyhalo (index ešte nemusí existovať)")
        return []
    return [_row_out(r, "_score") for r in rows]


def get_chunks_by_ids(chunk_ids: list[str]) -> list[dict[str, Any]]:
    tbl = _table_or_none()
    if tbl is None or not chunk_ids:
        return []
    quoted = ",".join(_sql_str(cid) for cid in chunk_ids)
    rows = tbl.search().where(f"chunk_id IN ({quoted})").limit(len(chunk_ids)).to_list()
    return [_row_out(r, "_distance") for r in rows]


def document_chunks(doc_id: str) -> list[dict[str, Any]]:
    """Všetky chunky dokumentu v poradí vzniku (index z chunk_id suffixu)."""
    tbl = _table_or_none()
    if tbl is None:
        return []
    rows = tbl.search().where(f"doc_id = {_sql_str(doc_id)}").limit(100_000).to_list()

    def order(row: dict[str, Any]) -> tuple[int, int]:
        suffix = row["chunk_id"].rsplit(":", 1)[-1]
        num = int(suffix[1:]) if suffix.startswith("t") and suffix[1:].isdigit() else 10**9
        return (row["page_from"], num)

    return sorted((_row_out(r, "_distance") for r in rows), key=order)


def chunks_on_pages(pages: list[tuple[str, int]], per_page: int = 2) -> list[dict[str, Any]]:
    """Textové chunky, ktoré pokrývajú dané strany — v poradí strán (vizuálny retrieval)."""
    tbl = _table_or_none()
    if tbl is None or not pages:
        return []
    out: list[dict[str, Any]] = []
    for doc_id, page in pages:
        where = f"doc_id = {_sql_str(doc_id)} AND page_from <= {int(page)}"
        rows = tbl.search().where(f"{where} AND page_to >= {int(page)}").limit(per_page).to_list()
        out += [_row_out(r, "_distance") for r in rows]
    return out


def count_chunks(doc_id: str | None = None) -> int:
    tbl = _table_or_none()
    if tbl is None:
        return 0
    if doc_id is None:
        return int(tbl.count_rows())
    return int(tbl.count_rows(f"doc_id = {_sql_str(doc_id)}"))

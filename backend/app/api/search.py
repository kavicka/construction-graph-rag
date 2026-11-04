"""Debug/dev search — phase 3 builds chat retrieval on top of this."""

from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.auth import allowed_doc_ids
from app.index.embedder import get_embedder
from app.index.lancedb_store import search_dense, search_fts
from app.retrieve.fuse import rrf_fuse
from app.retrieve.secrets import redact_secrets

router = APIRouter()


class SearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    page_from: int
    page_to: int
    section_path: str
    type: str
    table_id: str | None
    rrf_score: float | None = None
    score: float | None = None


def _search_sync(q: str, k: int, mode: str) -> list[dict[str, Any]]:
    allowed = allowed_doc_ids()
    if mode in ("dense", "hybrid"):
        vector = get_embedder().embed_query(q)
        dense = search_dense(vector, k=max(k * 3, 24), doc_ids=allowed)
    else:
        dense = []
    fts = search_fts(q, k=max(k * 3, 24), doc_ids=allowed) if mode in ("fts", "hybrid") else []

    if mode == "hybrid":
        return rrf_fuse([dense, fts], top_k=k)
    return (dense or fts)[:k]


@router.get("/search")
async def search(
    q: str = Query(min_length=1),
    k: int = Query(default=8, le=50),
    mode: Literal["hybrid", "dense", "fts"] = "hybrid",
) -> list[SearchHit]:
    rows = await run_in_threadpool(_search_sync, q, k, mode)
    return [SearchHit.model_validate({**r, "text": redact_secrets(r["text"])}) for r in rows]

"""Orchestrácia ingest jobov.

Semafor 1 — nikdy dva ingesty naraz (RAM disciplína, platí aj na high profile:
paralelné parsovanie nezrýchli ML modely, len zdvojnásobí pamäť).

Pipeline: parsing → tables (LLM sumáre) → embedding (chunky → LanceDB) → done
"""

import asyncio
import logging

from opentelemetry.context import attach, detach

from app import db
from app.config import get_settings
from app.graph.build import build_document_graph
from app.index.chunker import (
    build_table_chunks,
    chunk_text_items,
    contextualize_chunks,
    load_document,
)
from app.index.embedder import get_embedder
from app.index.lancedb_store import replace_document_chunks
from app.ingest.docling_parse import parse_document
from app.ingest.records import extract_records
from app.obs.tracing import start_root

logger = logging.getLogger(__name__)

_semaphore = asyncio.Semaphore(1)
_tasks: set[asyncio.Task[None]] = set()


def schedule_ingest(doc_id: str) -> None:
    task = asyncio.create_task(_run_ingest(doc_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _index_document(doc_id: str) -> int:
    """Chunking + sumáre tabuliek + embeddingy + zápis do LanceDB. Beží v to_thread."""
    document = load_document(doc_id)
    chunks = chunk_text_items(doc_id, document)

    db.set_document_status(doc_id, "tables", progress=0.6)
    chunks += build_table_chunks(doc_id)
    extract_records(doc_id)

    if get_settings().contextual_chunks:
        db.set_document_status(doc_id, "context", progress=0.7)
        contextualize_chunks(doc_id, document, chunks)

    db.set_document_status(doc_id, "embedding", progress=0.8)
    embedder = get_embedder()
    vectors = embedder.embed_passages([c.text for c in chunks])
    stored = replace_document_chunks(doc_id, chunks, vectors)
    # súradnice pre zvýraznenie citácie v PDF — v SQLite, schéma LanceDB ostáva stabilná
    db.replace_chunk_boxes(doc_id, {c.chunk_id: c.boxes for c in chunks})

    doc = db.get_document(doc_id)
    if get_settings().visual_retrieval and doc and doc.get("format", "pdf") == "pdf":
        from app.index.visual import index_pages

        db.set_document_status(doc_id, "visual", progress=0.85)
        index_pages(doc_id, get_settings().pdf_dir / f"{doc_id}.pdf")
    return stored


async def _run_ingest(doc_id: str) -> None:
    async with _semaphore:
        doc = db.get_document(doc_id)
        root, ctx = start_root(
            "rag.ingest",
            rag__doc_id=doc_id,
            rag__question=doc["filename"] if doc else doc_id,
            rag__mode="ingest",
        )
        token = attach(ctx)
        try:
            db.set_document_status(doc_id, "parsing", progress=0.1)
            summary = await asyncio.to_thread(parse_document, doc_id)
            stored = await asyncio.to_thread(_index_document, doc_id)
            db.set_document_status(doc_id, "graph", progress=0.9)
            graph_counts = await asyncio.to_thread(build_document_graph, doc_id)
            logger.info(
                "doc %s: %s strán, %s tabuliek, %s chunkov, graf %s",
                doc_id,
                summary["pages"],
                summary["tables"],
                stored,
                graph_counts,
            )
            db.set_document_status(doc_id, "done", progress=1.0)
        except Exception as exc:
            logger.exception("ingest zlyhal pre doc %s", doc_id)
            db.set_document_status(doc_id, "error", error=str(exc))
            root.record_exception(exc)
        finally:
            detach(token)
            root.end()

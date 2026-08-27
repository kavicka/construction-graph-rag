"""Orchestrácia ingest jobov.

Semafor 1 — nikdy dva ingesty naraz (RAM disciplína, platí aj na high profile:
paralelné parsovanie nezrýchli ML modely, len zdvojnásobí pamäť).

Pipeline: parsing → tables (LLM sumáre) → embedding (chunky → LanceDB) → done
"""

import asyncio
import logging

from app import db
from app.graph.build import build_document_graph
from app.index.chunker import build_table_chunks, chunk_text_items, load_document
from app.index.embedder import get_embedder
from app.index.lancedb_store import replace_document_chunks
from app.ingest.docling_parse import parse_pdf
from app.ingest.records import extract_records

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

    db.set_document_status(doc_id, "embedding", progress=0.8)
    embedder = get_embedder()
    vectors = embedder.embed_passages([c.text for c in chunks])
    return replace_document_chunks(doc_id, chunks, vectors)


async def _run_ingest(doc_id: str) -> None:
    async with _semaphore:
        try:
            db.set_document_status(doc_id, "parsing", progress=0.1)
            summary = await asyncio.to_thread(parse_pdf, doc_id)
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

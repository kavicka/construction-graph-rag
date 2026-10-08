"""Uloženie a zmazanie dokumentu — spoločné pre upload API aj sledovaný inbox."""

import hashlib
import logging
import uuid
from pathlib import Path
from typing import Any

from app import db
from app.config import get_settings
from app.index.lancedb_store import delete_document_chunks

logger = logging.getLogger(__name__)


def document_path(doc_id: str, fmt: str) -> Path:
    return get_settings().pdf_dir / f"{doc_id}.{fmt}"


def store_document(
    filename: str, data: bytes, fmt: str, *, source_path: str | None = None
) -> tuple[dict[str, Any], bool]:
    """→ (riadok dokumentu, vytvorený nový?). Rovnaký obsah = ten istý dokument (sha256)."""
    sha = hashlib.sha256(data).hexdigest()
    existing = db.find_document_by_sha(sha)
    if existing:
        return existing, False
    doc_id = uuid.uuid4().hex
    document_path(doc_id, fmt).write_bytes(data)
    db.insert_document(doc_id, filename, sha, fmt=fmt, source_path=source_path)
    row = db.get_document(doc_id)
    assert row is not None
    return row, True


def delete_document(doc_id: str) -> bool:
    """Zmaže dokument zo všetkých úložísk: súbor, chunky + FTS, graf, tabuľky, záznamy."""
    doc = db.get_document(doc_id)
    if doc is None:
        return False
    delete_document_chunks(doc_id)
    db.delete_document_graph(doc_id)
    for npy in (get_settings().data_dir / "visual").glob(f"{doc_id}__*.npy"):
        npy.unlink()
    for table in db.list_tables(doc_id):
        for key in ("markdown_path", "parquet_path"):
            if table.get(key):
                Path(table[key]).unlink(missing_ok=True)
    settings = get_settings()
    document_path(doc_id, doc.get("format") or "pdf").unlink(missing_ok=True)
    (settings.parsed_dir / f"{doc_id}.json").unlink(missing_ok=True)
    db.delete_document(doc_id)
    logger.info("dokument %s (%s) zmazaný", doc_id, doc["filename"])
    return True

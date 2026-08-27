import hashlib
import uuid

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.config import get_settings
from app.ingest.service import schedule_ingest

router = APIRouter()

MAX_PDF_BYTES = 100 * 1024 * 1024


class DocumentOut(BaseModel):
    id: str
    filename: str
    status: str
    stage_progress: float
    pages_count: int | None
    error: str | None
    created_at: str


class PageOut(BaseModel):
    page_no: int
    text_len: int


class TableMetaOut(BaseModel):
    id: str
    page_no: int | None
    caption: str | None
    n_rows: int
    n_cols: int
    summary: str | None


class DocumentDetail(DocumentOut):
    pages: list[PageOut]
    tables: list[TableMetaOut]


def _to_out(row: dict) -> DocumentOut:  # type: ignore[type-arg]
    return DocumentOut(**{k: row[k] for k in DocumentOut.model_fields})


@router.post("/documents", status_code=201)
async def upload_documents(files: list[UploadFile]) -> list[DocumentOut]:
    settings = get_settings()
    out: list[DocumentOut] = []
    for f in files:
        data = await f.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(status_code=415, detail=f"{f.filename}: nie je PDF")
        if len(data) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail=f"{f.filename}: nad 100 MB")

        sha = hashlib.sha256(data).hexdigest()
        existing = await run_in_threadpool(db.find_document_by_sha, sha)
        if existing:
            out.append(_to_out(existing))
            continue

        doc_id = uuid.uuid4().hex
        (settings.pdf_dir / f"{doc_id}.pdf").write_bytes(data)
        await run_in_threadpool(
            db.insert_document, doc_id, f.filename or "dokument.pdf", sha
        )
        schedule_ingest(doc_id)
        row = await run_in_threadpool(db.get_document, doc_id)
        assert row is not None
        out.append(_to_out(row))
    return out


@router.get("/documents")
async def get_documents() -> list[DocumentOut]:
    rows = await run_in_threadpool(db.list_documents)
    return [_to_out(r) for r in rows]


@router.get("/documents/{doc_id}")
async def get_document_detail(doc_id: str) -> DocumentDetail:
    row = await run_in_threadpool(db.get_document, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dokument neexistuje")
    pages = await run_in_threadpool(db.get_pages, doc_id)
    tables = await run_in_threadpool(db.list_tables, doc_id)
    return DocumentDetail(
        **{k: row[k] for k in DocumentOut.model_fields},
        pages=[PageOut(**p) for p in pages],
        tables=[TableMetaOut(**{k: t[k] for k in TableMetaOut.model_fields}) for t in tables],
    )


@router.post("/documents/{doc_id}/reindex", status_code=202)
async def reindex_document(doc_id: str) -> DocumentOut:
    """Znovu spustí celú pipeline (parse → index → graf) — po upgrade ingestu."""
    row = await run_in_threadpool(db.get_document, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dokument neexistuje")
    if not (get_settings().pdf_dir / f"{doc_id}.pdf").exists():
        raise HTTPException(status_code=404, detail="PDF súbor chýba na disku")
    await run_in_threadpool(db.set_document_status, doc_id, "queued")
    schedule_ingest(doc_id)
    fresh = await run_in_threadpool(db.get_document, doc_id)
    assert fresh is not None
    return _to_out(fresh)


@router.get("/documents/{doc_id}/pdf")
async def get_document_pdf(doc_id: str) -> FileResponse:
    row = await run_in_threadpool(db.get_document, doc_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dokument neexistuje")
    path = get_settings().pdf_dir / f"{doc_id}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF súbor chýba na disku")
    return FileResponse(path, media_type="application/pdf", filename=row["filename"])

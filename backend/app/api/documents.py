import json

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.api.access import ensure_document, require_admin
from app.auth import allowed_doc_ids, can_access_document, current_scope, document_access
from app.index.lancedb_store import get_chunks_by_ids
from app.ingest.formats import FORMATS, detect_format, media_type
from app.ingest.service import schedule_ingest
from app.ingest.store import delete_document, document_path, store_document

router = APIRouter()

MAX_FILE_BYTES = 100 * 1024 * 1024


class DocumentOut(BaseModel):
    id: str
    filename: str
    format: str
    source_path: str | None
    access: list[str]
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
    fields = {k: row[k] for k in DocumentOut.model_fields if k != "access"}
    return DocumentOut(**fields, access=document_access(row))


@router.post("/documents", status_code=201)
async def upload_documents(files: list[UploadFile]) -> list[DocumentOut]:
    out: list[DocumentOut] = []
    for f in files:
        data = await f.read()
        name = f.filename or "dokument"
        fmt = detect_format(name, data)
        if fmt is None:
            supported = ", ".join(sorted(FORMATS))
            raise HTTPException(
                status_code=415, detail=f"{name}: nepodporovaný formát (podporované: {supported})"
            )
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status_code=413, detail=f"{name}: nad 100 MB")
        row, created = await run_in_threadpool(store_document, name, data, fmt)
        if not created and not await run_in_threadpool(can_access_document, row["id"]):
            # identický obsah ako dokument, ktorý používateľ nevidí — nič o ňom neprezradiť
            raise HTTPException(status_code=409, detail=f"{name}: rovnaký obsah už existuje")
        if created and current_scope().groups and not current_scope().unrestricted:
            # nahraté členom: vidí ho jeho skupina, admin môže prístup rozšíriť
            groups = sorted(current_scope().groups)
            await run_in_threadpool(db.set_document_access, row["id"], json.dumps(groups))
            row = {**row, "access_json": json.dumps(groups)}
        if created:
            schedule_ingest(row["id"])
        out.append(_to_out(row))
    return out


@router.get("/documents")
async def get_documents() -> list[DocumentOut]:
    rows = await run_in_threadpool(db.list_documents)
    allowed = await run_in_threadpool(allowed_doc_ids)
    return [_to_out(r) for r in rows if allowed is None or r["id"] in allowed]


@router.get("/documents/{doc_id}")
async def get_document_detail(doc_id: str) -> DocumentDetail:
    row = await run_in_threadpool(ensure_document, doc_id)
    pages = await run_in_threadpool(db.get_pages, doc_id)
    tables = await run_in_threadpool(db.list_tables, doc_id)
    return DocumentDetail(
        **_to_out(row).model_dump(),
        pages=[PageOut(**p) for p in pages],
        tables=[TableMetaOut(**{k: t[k] for k in TableMetaOut.model_fields}) for t in tables],
    )


@router.post("/documents/{doc_id}/reindex", status_code=202)
async def reindex_document(doc_id: str) -> DocumentOut:
    """Znovu spustí celú pipeline (parse → index → graf) — po upgrade ingestu."""
    require_admin()
    row = await run_in_threadpool(ensure_document, doc_id)
    if not document_path(doc_id, row["format"]).exists():
        raise HTTPException(status_code=404, detail="súbor chýba na disku")
    await run_in_threadpool(db.set_document_status, doc_id, "queued")
    schedule_ingest(doc_id)
    fresh = await run_in_threadpool(db.get_document, doc_id)
    assert fresh is not None
    return _to_out(fresh)


@router.delete("/documents/{doc_id}", status_code=204)
async def remove_document(doc_id: str) -> None:
    require_admin()
    row = await run_in_threadpool(ensure_document, doc_id)
    if row["status"] not in ("done", "error"):
        raise HTTPException(status_code=409, detail="dokument sa ešte spracúva")
    await run_in_threadpool(delete_document, doc_id)


async def _file_response(doc_id: str, *, pdf_only: bool) -> FileResponse:
    row = await run_in_threadpool(ensure_document, doc_id)
    if pdf_only and row["format"] != "pdf":
        raise HTTPException(status_code=404, detail="dokument nie je PDF — použi /file")
    path = document_path(doc_id, row["format"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="súbor chýba na disku")
    if row["format"] == "pdf":
        return FileResponse(path, media_type="application/pdf", filename=row["filename"],
                            content_disposition_type="inline")
    # HTML/MD/Office od používateľa nesmú bežať v origine appky (XSS → admin session):
    # stiahnutie + sandbox CSP + bez MIME sniffingu
    return FileResponse(
        path, media_type=media_type(row["format"]), filename=row["filename"],
        content_disposition_type="attachment",
        headers={"Content-Security-Policy": "sandbox; default-src 'none'",
                 "X-Content-Type-Options": "nosniff"},
    )


@router.get("/documents/{doc_id}/pdf")
async def get_document_pdf(doc_id: str) -> FileResponse:
    return await _file_response(doc_id, pdf_only=True)


@router.get("/documents/{doc_id}/file")
async def get_document_file(doc_id: str) -> FileResponse:
    """Originálny súbor v pôvodnom formáte (DOCX, PPTX, …)."""
    return await _file_response(doc_id, pdf_only=False)


class ChunkOut(BaseModel):
    chunk_id: str
    doc_id: str
    filename: str
    format: str
    page_from: int
    page_to: int
    section_path: str
    type: str
    text: str


@router.get("/chunks/{chunk_id}")
async def get_chunk(chunk_id: str) -> ChunkOut:
    """Citovaná pasáž — prehliadač pre formáty bez strán (DOCX, HTML, MD…)."""
    rows = await run_in_threadpool(get_chunks_by_ids, [chunk_id])
    if not rows:
        raise HTTPException(status_code=404, detail="pasáž neexistuje")
    row = rows[0]
    doc = await run_in_threadpool(ensure_document, row["doc_id"])
    return ChunkOut(
        chunk_id=row["chunk_id"], doc_id=row["doc_id"],
        filename=doc["filename"] if doc else row["doc_id"],
        format=doc["format"] if doc else "pdf",
        page_from=row["page_from"], page_to=row["page_to"],
        section_path=row["section_path"], type=row["type"], text=row["raw_text"],
    )

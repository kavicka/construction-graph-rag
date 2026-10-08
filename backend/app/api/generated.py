"""Vygenerované branded PDF dokumenty."""

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.api.access import chat_visible
from app.auth import current_scope
from app.config import get_settings
from app.docgen.generate import slug_filename

router = APIRouter(prefix="/generated")


class GeneratedOut(BaseModel):
    id: str
    chat_id: str | None
    title: str
    doc_type: str
    pages: int
    filename: str
    url: str
    created_at: str


def _out(row: dict) -> GeneratedOut:  # type: ignore[type-arg]
    return GeneratedOut(
        id=row["id"],
        chat_id=row["chat_id"],
        title=row["title"],
        doc_type=row["doc_type"],
        pages=row["pages"],
        filename=slug_filename(row["title"]),
        url=f"/api/generated/{row['id']}/pdf",
        created_at=row["created_at"],
    )


def _visible(row: dict) -> bool:  # type: ignore[type-arg]
    """Bez prihlasovania vidno všetko; inak len vlastné dokumenty (autor alebo jeho chat)."""
    scope = current_scope()
    if scope.user_id is None:
        return True
    if row.get("user_id") is not None:
        return bool(row["user_id"] == scope.user_id)
    chat = db.get_chat(row["chat_id"]) if row["chat_id"] else None
    return chat is not None and chat_visible(chat)


@router.get("")
async def list_generated() -> list[GeneratedOut]:
    rows = await run_in_threadpool(db.list_generated_documents)
    return [_out(r) for r in rows if await run_in_threadpool(_visible, r)]


@router.get("/{doc_id}/pdf")
async def get_generated_pdf(doc_id: str, download: bool = False) -> FileResponse:
    row = await run_in_threadpool(db.get_generated_document, doc_id)
    if row is None or not await run_in_threadpool(_visible, row):
        raise HTTPException(status_code=404, detail="dokument neexistuje")
    path = get_settings().generated_dir / f"{doc_id}.pdf"
    if not path.exists():
        raise HTTPException(status_code=404, detail="PDF súbor chýba na disku")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=slug_filename(row["title"]),
        content_disposition_type="attachment" if download else "inline",
    )

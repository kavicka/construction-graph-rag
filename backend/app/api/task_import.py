"""Import úloh z dokumentu: upload → návrhy (na pozadí) → úpravy a schválenie → úlohy."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.access import require_admin
from app.api.documents import MAX_FILE_BYTES
from app.api.uploads import read_capped
from app.ingest.formats import FORMATS, detect_format
from app.ingest.service import schedule_ingest
from app.ingest.store import store_document
from app.workforce import store
from app.workforce.importer import (
    ApplyError,
    apply_import,
    draft_issues,
    score_draft,
    suggest_previous,
)
from app.workforce.runtime import schedule

router = APIRouter(prefix="/task-imports")


class SourceOut(BaseModel):
    id: str
    doc_id: str | None
    site_id: str
    site_name: str
    filename: str
    previous_id: str | None
    version: int
    doc_date: str | None
    status: str
    progress: float
    error: str | None
    created_at: str


def _source_out(row: dict[str, Any]) -> SourceOut:
    site = store.get_site(row["site_id"])
    return SourceOut(
        **{k: row[k] for k in SourceOut.model_fields if k in row},
        site_name=site["name"] if site else "",
    )


class DraftOut(BaseModel):
    id: str
    kind: Literal["new", "changed", "cancelled", "unchanged"]
    target_task_id: str | None
    decision: Literal["pending", "approved", "rejected"]
    data: dict[str, Any]


class ImportDetail(BaseModel):
    source: SourceOut
    drafts: list[DraftOut]


def _import_job(source_id: str) -> None:
    from app.workforce.importer import run_import

    run_import(source_id)


@router.get("/suggest-previous")
async def suggest(site_id: str, filename: str) -> SourceOut | None:
    """Je nahrávaný súbor nová verzia už importovaného dokumentu?"""
    require_admin()
    row = await run_in_threadpool(suggest_previous, site_id, filename)
    return await run_in_threadpool(_source_out, row) if row else None


@router.post("", status_code=201)
async def upload(
    file: UploadFile, site_id: str = Form(...), previous_id: str | None = Form(None)
) -> SourceOut:
    require_admin()
    if await run_in_threadpool(store.get_site, site_id) is None:
        raise HTTPException(status_code=422, detail="stavba neexistuje")
    version = 1
    if previous_id:
        prev = await run_in_threadpool(store.get_source, previous_id)
        if prev is None or prev["site_id"] != site_id:
            raise HTTPException(status_code=422, detail="predošlá verzia neexistuje")
        version = prev["version"] + 1
    name = file.filename or "dokument"
    data = await read_capped(file, MAX_FILE_BYTES, f"{name}: nad 100 MB")
    fmt = detect_format(name, data)
    if fmt is None:
        raise HTTPException(
            status_code=415,
            detail=f"{name}: nepodporovaný formát (podporované: {', '.join(sorted(FORMATS))})",
        )
    doc, created = await run_in_threadpool(store_document, name, data, fmt)
    if created:
        schedule_ingest(doc["id"])
    row = await run_in_threadpool(
        lambda: store.insert_source(
            doc_id=doc["id"],
            site_id=site_id,
            filename=name,
            previous_id=previous_id or None,
            version=version,
        )
    )
    schedule(_import_job, row["id"])
    return await run_in_threadpool(_source_out, row)


@router.get("")
async def list_imports(site_id: str | None = None) -> list[SourceOut]:
    require_admin()
    rows = await run_in_threadpool(store.list_sources, site_id)
    return [await run_in_threadpool(_source_out, r) for r in rows]


def _detail(source_id: str) -> ImportDetail:
    row = store.get_source(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="import neexistuje")
    drafts = [
        DraftOut(**{k: d[k] for k in DraftOut.model_fields}) for d in store.list_drafts(source_id)
    ]
    return ImportDetail(source=_source_out(row), drafts=drafts)


@router.get("/{source_id}")
async def get_import(source_id: str) -> ImportDetail:
    require_admin()
    return await run_in_threadpool(_detail, source_id)


class DraftUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    work_type: str | None = Field(default=None, max_length=80)
    date_from: date | None = None
    date_to: date | None = None
    assignees: list[str] = Field(default_factory=list, max_length=30)
    decision: Literal["pending", "approved", "rejected"]
    clear_dependency: bool = False


@router.put("/{source_id}/drafts/{draft_id}")
async def update_draft(source_id: str, draft_id: str, body: DraftUpdate) -> DraftOut:
    require_admin()
    draft = await run_in_threadpool(store.get_draft, draft_id)
    if draft is None or draft["source_id"] != source_id:
        raise HTTPException(status_code=404, detail="návrh neexistuje")
    workers = await run_in_threadpool(store.worker_ids)
    if unknown := [u for u in body.assignees if u not in workers]:
        raise HTTPException(status_code=422, detail=f"neznámi pracovníci: {unknown}")
    data = dict(draft["data"])
    data.update(
        title=body.title.strip(),
        description=body.description.strip(),
        work_type=body.work_type,
        date_from=body.date_from.isoformat() if body.date_from else None,
        date_to=body.date_to.isoformat() if body.date_to else None,
        assignees=body.assignees,
    )
    if body.clear_dependency:
        data["depends_on"] = None
    if data.get("assignees") != draft["data"].get("assignees"):
        data["assign_reason"] = "upravené majiteľom"
    if body.date_from or body.date_to:
        data["date_how"] = "absolute"  # termín zadal majiteľ
    if draft["kind"] != "cancelled":
        score_draft(data)
    await run_in_threadpool(lambda: store.update_draft(draft_id, data=data, decision=body.decision))
    updated = await run_in_threadpool(store.get_draft, draft_id)
    assert updated is not None
    return DraftOut(**{k: updated[k] for k in DraftOut.model_fields})


class DecideAllIn(BaseModel):
    decision: Literal["approved", "rejected"]


@router.post("/{source_id}/decide-all")
async def decide_all(source_id: str, body: DecideAllIn) -> ImportDetail:
    """„Schváliť všetko“ — len nerozhodnuté návrhy; tie bez termínu ostanú nerozhodnuté."""
    require_admin()

    def run() -> None:
        for d in store.list_drafts(source_id):
            if d["decision"] != "pending":
                continue
            if (
                body.decision == "approved"
                and d["kind"] in ("new", "changed")
                and (draft_issues(d["data"]) or d["data"].get("duplicate_of"))
            ):
                continue  # bez termínu alebo možná duplicita — rozhodne majiteľ sám
            store.update_draft(d["id"], data=d["data"], decision=body.decision)

    await run_in_threadpool(run)
    return await run_in_threadpool(_detail, source_id)


@router.post("/{source_id}/apply")
async def apply(source_id: str) -> dict[str, int]:
    require_admin()
    try:
        return await run_in_threadpool(apply_import, source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="import neexistuje") from exc
    except ApplyError as exc:
        raise HTTPException(
            status_code=409, detail={"message": str(exc), "problems": exc.problems}
        ) from exc


@router.post("/{source_id}/retry", status_code=202)
async def retry(source_id: str) -> SourceOut:
    require_admin()
    row = await run_in_threadpool(store.get_source, source_id)
    if row is None:
        raise HTTPException(status_code=404, detail="import neexistuje")
    if row["status"] not in ("error", "review"):
        raise HTTPException(status_code=409, detail="import práve beží alebo je použitý")
    await run_in_threadpool(lambda: store.set_source_status(source_id, "waiting", progress=0))
    schedule(_import_job, source_id)
    updated = await run_in_threadpool(store.get_source, source_id)
    assert updated is not None
    return await run_in_threadpool(_source_out, updated)

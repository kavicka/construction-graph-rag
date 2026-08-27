from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import db

router = APIRouter()

MAX_ROWS = 500


class TableData(BaseModel):
    id: str
    doc_id: str
    page_no: int | None
    caption: str | None
    n_rows: int
    n_cols: int
    summary: str | None
    markdown: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool


def _read_parquet(path: str) -> tuple[list[str], list[list[Any]], bool]:
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    truncated = table.num_rows > MAX_ROWS
    head = table.slice(0, MAX_ROWS)
    columns = [str(c) for c in head.column_names]
    rows = [list(r.values()) for r in head.to_pylist()]
    return columns, rows, truncated


@router.get("/tables/{table_id}")
async def get_table(table_id: str) -> TableData:
    meta = await run_in_threadpool(db.get_table, table_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="tabuľka neexistuje")

    markdown = ""
    md_path = Path(meta["markdown_path"])
    if md_path.exists():
        markdown = md_path.read_text(encoding="utf-8")

    columns: list[str] = []
    rows: list[list[Any]] = []
    truncated = False
    if meta["parquet_path"] and Path(meta["parquet_path"]).exists():
        columns, rows, truncated = await run_in_threadpool(_read_parquet, meta["parquet_path"])

    return TableData(
        id=meta["id"],
        doc_id=meta["doc_id"],
        page_no=meta["page_no"],
        caption=meta["caption"],
        n_rows=meta["n_rows"],
        n_cols=meta["n_cols"],
        summary=meta["summary"],
        markdown=markdown,
        columns=columns,
        rows=rows,
        truncated=truncated,
    )

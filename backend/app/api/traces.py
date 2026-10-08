"""Vstavaný prehliadač traces + feedback k odpovediam (napojený na trace)."""

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app import db
from app.api.access import ensure_message, require_admin

router = APIRouter()


class TraceSummary(BaseModel):
    trace_id: str
    name: str
    question: str | None
    mode: str | None
    status: str
    started_at_ns: int
    duration_ms: float
    n_spans: int
    input_tokens: int
    output_tokens: int
    rating: int | None


class SpanOut(BaseModel):
    span_id: str
    parent_id: str | None
    name: str
    start_ms: float  # od začiatku trace
    duration_ms: float
    status: str
    attributes: dict[str, Any]


class TraceDetail(BaseModel):
    trace_id: str
    duration_ms: float
    spans: list[SpanOut]


@router.get("/traces")
async def list_traces(limit: int = Query(default=50, ge=1, le=500)) -> list[TraceSummary]:
    require_admin()
    rows = await run_in_threadpool(db.list_root_spans, limit)
    out = []
    for r in rows:
        attrs = json.loads(r["attrs_json"])
        out.append(
            TraceSummary(
                trace_id=r["trace_id"],
                name=r["name"],
                question=attrs.get("rag.question"),
                mode=attrs.get("rag.mode"),
                status=r["status"],
                started_at_ns=r["start_ns"],
                duration_ms=(r["end_ns"] - r["start_ns"]) / 1e6,
                n_spans=r["n_spans"],
                input_tokens=int(r["input_tokens"] or 0),
                output_tokens=int(r["output_tokens"] or 0),
                rating=r["rating"],
            )
        )
    return out


@router.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> TraceDetail:
    require_admin()
    rows = await run_in_threadpool(db.get_trace_spans, trace_id)
    if not rows:
        raise HTTPException(status_code=404, detail="trace neexistuje")
    t0 = min(r["start_ns"] for r in rows)
    t1 = max(r["end_ns"] for r in rows)
    return TraceDetail(
        trace_id=trace_id,
        duration_ms=(t1 - t0) / 1e6,
        spans=[
            SpanOut(
                span_id=r["span_id"],
                parent_id=r["parent_id"],
                name=r["name"],
                start_ms=(r["start_ns"] - t0) / 1e6,
                duration_ms=(r["end_ns"] - r["start_ns"]) / 1e6,
                status=r["status"],
                attributes=json.loads(r["attrs_json"]),
            )
            for r in rows
        ],
    )


class FeedbackIn(BaseModel):
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    message_id: str
    rating: int
    comment: str | None
    question: str | None
    answer: str
    trace_id: str | None
    chat_id: str
    created_at: str


@router.put("/messages/{message_id}/feedback", status_code=204)
async def put_feedback(message_id: str, body: FeedbackIn) -> None:
    message = await run_in_threadpool(ensure_message, message_id)
    if message["role"] != "assistant":
        raise HTTPException(status_code=404, detail="odpoveď neexistuje")
    await run_in_threadpool(db.set_feedback, message_id, body.rating, body.comment)


@router.delete("/messages/{message_id}/feedback", status_code=204)
async def delete_feedback(message_id: str) -> None:
    await run_in_threadpool(ensure_message, message_id)
    await run_in_threadpool(db.delete_feedback, message_id)


@router.get("/feedback")
async def list_feedback(rating: Literal[-1, 1] | None = None) -> list[FeedbackOut]:
    require_admin()
    rows = await run_in_threadpool(db.list_feedback, rating)
    return [FeedbackOut(**{k: r[k] for k in FeedbackOut.model_fields}) for r in rows]


@router.get("/feedback/export")
async def export_feedback() -> list[dict[str, Any]]:
    """Negatívne hodnotené odpovede ako kandidáti do golden setu (eval/construction.py
    formát) — doplní sa len očakávaná odpoveď a gold strany."""
    require_admin()
    rows = await run_in_threadpool(db.list_feedback, -1)
    return [
        {
            "id": f"fb-{r['message_id'][:8]}",
            "question": r["question"],
            "kind": "factual",
            "gold": [
                [s["filename"], s["page_from"]]
                for s in (json.loads(r["sources_json"]) if r["sources_json"] else [])[:3]
            ],
            "expected": [],
            "bad_answer": r["answer"],
            "comment": r["comment"],
            "trace_id": r["trace_id"],
        }
        for r in rows
        if r["question"]
    ]

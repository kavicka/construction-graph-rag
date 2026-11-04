"""Chat endpoint — SSE stream of orchestrator events + message persistence."""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db
from app.api.access import chat_visible, ensure_chat, ensure_message
from app.auth import current_scope
from app.docgen.generate import export_answer_pdf
from app.docgen.pdf import PdfEngineError
from app.llm.ollama_client import OllamaError
from app.retrieve.orchestrator import run_query
from app.retrieve.pipeline import Source, sources_to_json

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    chat_id: str | None = None
    mode: Literal["auto", "research"] = "auto"


class ChatOut(BaseModel):
    id: str
    title: str
    created_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] | None  # type: ignore[type-arg]
    document: dict | None = None  # type: ignore[type-arg]
    trace_id: str | None = None
    rating: int | None = None
    verification: dict | None = None  # type: ignore[type-arg]
    created_at: str


def _sse(payload: dict) -> str:  # type: ignore[type-arg]
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _stream_answer(
    chat_id: str, question: str, history: list[dict[str, Any]], mode: str | None = None
) -> AsyncIterator[str]:
    try:
        async for event in run_query(question, chat_id=chat_id, history=history, mode=mode):
            if event["type"] == "final":
                sources: list[Source] = event["sources"]
                document = event["document"]
                message_id = uuid.uuid4().hex
                await run_in_threadpool(
                    db.add_message,
                    message_id,
                    chat_id,
                    "assistant",
                    event["answer"],
                    sources_to_json(sources),
                    json.dumps(document, ensure_ascii=False) if document else None,
                )
                await run_in_threadpool(
                    db.set_message_meta,
                    message_id,
                    event["trace_id"],
                    json.dumps(event["verification"], ensure_ascii=False),
                )
                yield _sse(
                    {
                        "type": "done",
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "trace_id": event["trace_id"],
                    }
                )
            elif event["type"] == "sources":
                yield _sse({**event, "chat_id": chat_id})
            else:
                yield _sse(event)
    except (PdfEngineError, OllamaError) as exc:
        logger.error("chat stream: %s", exc)
        yield _sse({"type": "error", "message": str(exc)})
    except Exception:
        logger.exception("chat stream neočakávane zlyhal")
        yield _sse({"type": "error", "message": "interná chyba pri generovaní odpovede"})


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    chat_id = req.chat_id or uuid.uuid4().hex
    existing = await run_in_threadpool(db.get_chat, chat_id)
    if existing is not None and not chat_visible(existing):
        raise HTTPException(status_code=404, detail="chat neexistuje")
    history: list[dict[str, Any]] = []
    if existing is None:
        title = req.message[:60]
        await run_in_threadpool(db.create_chat, chat_id, title, current_scope().user_id)
    else:
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in await run_in_threadpool(db.list_messages, chat_id)
        ]
    await run_in_threadpool(db.add_message, uuid.uuid4().hex, chat_id, "user", req.message, None)

    return StreamingResponse(
        _stream_answer(
            chat_id, req.message, history, "research" if req.mode == "research" else None
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chats")
async def list_chats() -> list[ChatOut]:
    rows = await run_in_threadpool(db.list_chats, current_scope().user_id)
    return [ChatOut(**r) for r in rows]


@router.get("/chats/{chat_id}")
async def get_chat_messages(chat_id: str) -> list[MessageOut]:
    await run_in_threadpool(ensure_chat, chat_id)
    rows = await run_in_threadpool(db.list_messages, chat_id)
    ratings = await run_in_threadpool(db.message_ratings, chat_id)
    return [
        MessageOut(
            id=r["id"],
            role=r["role"],
            content=r["content"],
            sources=json.loads(r["sources_json"]) if r["sources_json"] else None,
            document=json.loads(r["document_json"]) if r["document_json"] else None,
            trace_id=r["trace_id"],
            rating=ratings.get(r["id"]),
            verification=json.loads(r["verify_json"]) if r["verify_json"] else None,
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/messages/{message_id}/export")
async def export_message(message_id: str) -> dict[str, Any]:
    """Chat answer → branded PDF (## headings become sections, citations are kept)."""
    message = await run_in_threadpool(ensure_message, message_id)
    if message["role"] != "assistant":
        raise HTTPException(status_code=404, detail="odpoveď neexistuje")
    question = await run_in_threadpool(db.preceding_question, message_id)
    try:
        doc = await run_in_threadpool(
            export_answer_pdf,
            message["content"],
            json.loads(message["sources_json"]) if message["sources_json"] else [],
            question or "Odpoveď z dokumentov",
            message["chat_id"],
        )
    except PdfEngineError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    await run_in_threadpool(db.set_message_document, message_id, json.dumps(doc))
    return doc

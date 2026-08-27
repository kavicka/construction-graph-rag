"""Chat endpoint — SSE stream: sources → delta* → done | error."""

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.llm.ollama_client import OllamaError, chat_stream
from app.retrieve.pipeline import build_messages, retrieve_sources, sources_to_json

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    chat_id: str | None = None


class ChatOut(BaseModel):
    id: str
    title: str
    created_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] | None  # type: ignore[type-arg]
    created_at: str


def _sse(payload: dict) -> str:  # type: ignore[type-arg]
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def strip_think(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """Odfiltruje úvodný <think>…</think> blok, ak by ho model predsa poslal
    (thinking varianty qwen3 ho leakujú do contentu)."""
    head = ""
    checking = True
    skipping = False
    async for tok in stream:
        if skipping:
            head += tok
            if "</think>" in head:
                rest = head.split("</think>", 1)[1].lstrip("\n")
                skipping = False
                checking = False
                head = ""
                if rest:
                    yield rest
            continue
        if checking:
            head += tok
            stripped = head.lstrip()
            if stripped.startswith("<think>"):
                skipping = True
                continue
            if len(stripped) >= 7 or not "<think>".startswith(stripped):
                checking = False
                buffered, head = head, ""
                yield buffered
            continue
        yield tok
    if head and not skipping:
        yield head


async def _stream_answer(chat_id: str, question: str) -> AsyncIterator[str]:
    settings = get_settings()
    try:
        sources = await run_in_threadpool(retrieve_sources, question)
        yield _sse(
            {"type": "sources", "chat_id": chat_id, "sources": [s.to_json() for s in sources]}
        )

        messages = build_messages(question, sources)
        answer_parts: list[str] = []
        async for token in strip_think(chat_stream(settings.answer_model, messages)):
            answer_parts.append(token)
            yield _sse({"type": "delta", "text": token})

        answer = "".join(answer_parts)
        message_id = uuid.uuid4().hex
        await run_in_threadpool(
            db.add_message, message_id, chat_id, "assistant", answer, sources_to_json(sources)
        )
        yield _sse({"type": "done", "chat_id": chat_id, "message_id": message_id})
    except OllamaError as exc:
        logger.error("chat stream: %s", exc)
        yield _sse({"type": "error", "message": str(exc)})
    except Exception:
        logger.exception("chat stream neočakávane zlyhal")
        yield _sse({"type": "error", "message": "interná chyba pri generovaní odpovede"})


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    chat_id = req.chat_id or uuid.uuid4().hex
    existing = await run_in_threadpool(db.get_chat, chat_id)
    if existing is None:
        title = req.message[:60]
        await run_in_threadpool(db.create_chat, chat_id, title)
    await run_in_threadpool(db.add_message, uuid.uuid4().hex, chat_id, "user", req.message, None)

    return StreamingResponse(
        _stream_answer(chat_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chats")
async def list_chats() -> list[ChatOut]:
    rows = await run_in_threadpool(db.list_chats)
    return [ChatOut(**r) for r in rows]


@router.get("/chats/{chat_id}")
async def get_chat_messages(chat_id: str) -> list[MessageOut]:
    if await run_in_threadpool(db.get_chat, chat_id) is None:
        raise HTTPException(status_code=404, detail="chat neexistuje")
    rows = await run_in_threadpool(db.list_messages, chat_id)
    return [
        MessageOut(
            id=r["id"],
            role=r["role"],
            content=r["content"],
            sources=json.loads(r["sources_json"]) if r["sources_json"] else None,
            created_at=r["created_at"],
        )
        for r in rows
    ]

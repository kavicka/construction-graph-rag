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
from app.retrieve.agent import run_agent
from app.retrieve.compose import run_compose
from app.retrieve.pipeline import Source, build_messages, retrieve_sources, sources_to_json
from app.retrieve.records_query import (
    build_filter_context,
    format_filter_answer,
    run_filter,
)
from app.retrieve.router import route_query
from app.retrieve.selfcheck import check_numbers

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
        plan = await run_in_threadpool(route_query, question)
        yield _sse({"type": "plan", "mode": plan.mode})

        sources: list[Source]
        evidence: list[str]
        answer_parts: list[str] = []

        if plan.mode == "filter":
            # plne deterministická cesta — filter aj odpoveď robí kód, LLM sa nevolá
            matched, nearest = await run_in_threadpool(run_filter, plan)
            context, sources = build_filter_context(plan, matched, nearest)
            yield _sse(
                {"type": "sources", "chat_id": chat_id,
                 "sources": [s.to_json() for s in sources]}
            )
            answer_text = format_filter_answer(plan, matched, nearest, sources)
            answer_parts.append(answer_text)
            yield _sse({"type": "delta", "text": answer_text})
            evidence = [context, question]

        elif plan.mode == "compose" and settings.agent_mode:
            yield _sse({"type": "step", "tool": "plan", "summary": "extrahujem plán výberu…"})
            categories = sorted({r["category"] for r in db.list_records() if r["category"]})
            composed = await run_in_threadpool(run_compose, question, categories)
            if composed is not None:
                answer_text, sources, evidence = composed
                yield _sse(
                    {"type": "step", "tool": "pick_items", "summary": "exaktný výber hotový"}
                )
                yield _sse(
                    {"type": "sources", "chat_id": chat_id,
                     "sources": [s.to_json() for s in sources]}
                )
                answer_parts.append(answer_text)
                yield _sse({"type": "delta", "text": answer_text})
            else:
                # plán sa nedal spoľahlivo vytiahnuť → voľná agent slučka
                yield _sse(
                    {"type": "step", "tool": "agent", "summary": "plán neistý — agent slučka"}
                )
                result = await run_agent(question)
                for step in result.trace:
                    yield _sse({"type": "step", "tool": step["tool"], "summary": step["args"]})
                sources = result.sources
                yield _sse(
                    {"type": "sources", "chat_id": chat_id,
                     "sources": [s.to_json() for s in sources]}
                )
                answer_parts.append(result.answer)
                yield _sse({"type": "delta", "text": result.answer})
                evidence = [*result.evidence, question]

        else:
            sources = await run_in_threadpool(retrieve_sources, question)
            yield _sse(
                {"type": "sources", "chat_id": chat_id,
                 "sources": [s.to_json() for s in sources]}
            )
            messages = build_messages(question, sources)
            async for token in strip_think(chat_stream(settings.answer_model, messages)):
                answer_parts.append(token)
                yield _sse({"type": "delta", "text": token})
            evidence = [*(s.text for s in sources), question]

        answer = "".join(answer_parts)
        issues = check_numbers(answer, evidence)
        yield _sse({"type": "verify", "ok": not issues, "issues": issues})

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

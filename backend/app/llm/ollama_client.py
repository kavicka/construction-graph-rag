"""LLM client — Ollama (default, local) or any OpenAI-compatible API.

RAG_LLM_PROVIDER=openai + RAG_OPENAI_BASE_URL / RAG_OPENAI_API_KEY switches generation
to a hosted model (OpenAI, Groq, Together, vLLM, LM Studio…); embeddings and reranker
stay local. Model names come from RAG_ANSWER_MODEL / RAG_EXTRACT_MODEL.

Sync `generate_sync` for indexing threads, async `chat_stream` for answers,
`chat_with_tools` for the agent. Every call is an OTel span with gen_ai.* attributes.
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from opentelemetry.context import Context

from app.config import get_settings
from app.obs.tracing import (
    GEN_AI_OPERATION,
    GEN_AI_PROVIDER,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    get_tracer,
    record_llm_usage,
    set_attrs,
    span,
)

logger = logging.getLogger(__name__)

# Greedy JSON decoding with a small model can loop on a free-text field (qwen3:4b-instruct
# repeated one sentence until the 240 s timeout). A structured call therefore gets a token cap
# by default. A repeat penalty is opt-in per call: applied globally it changed answers and the
# LLM judge (eval faithfulness 90 → 81 %, abstention 80 → 60 %).
JSON_MAX_TOKENS = 1024


class OllamaError(RuntimeError):
    """LLM call error (name is historical — applies to all providers)."""


LLMError = OllamaError


def _provider() -> str:
    return get_settings().llm_provider


def _openai_headers() -> dict[str, str]:
    key = get_settings().openai_api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


def _openai_url(path: str) -> str:
    return get_settings().openai_base_url.rstrip("/") + path


def _openai_usage(data: dict[str, Any]) -> dict[str, Any]:
    usage = data.get("usage") or {}
    choice = (data.get("choices") or [{}])[0]
    return {
        "prompt_eval_count": usage.get("prompt_tokens"),
        "eval_count": usage.get("completion_tokens"),
        "done_reason": choice.get("finish_reason"),
    }


def _num_ctx(requested: int) -> int:
    """One context size per model: Ollama reloads a model whenever num_ctx changes, so every
    call gets at least RAG_OLLAMA_NUM_CTX (by default the largest size any caller asks for)."""
    return max(requested, get_settings().ollama_num_ctx)


def _llm_span_attrs(operation: str, model: str, temperature: float) -> dict[str, Any]:
    return {
        GEN_AI_OPERATION: operation,
        GEN_AI_PROVIDER: _provider(),
        GEN_AI_REQUEST_MODEL: model,
        GEN_AI_REQUEST_TEMPERATURE: temperature,
    }


def generate_sync(
    model: str,
    prompt: str,
    *,
    system: str | None = None,
    json_schema: dict[str, Any] | None = None,
    temperature: float = 0.2,
    num_ctx: int = 4096,
    timeout: float = 120.0,
    think: bool = False,
    max_tokens: int | None = None,
    repeat_penalty: float | None = None,
) -> str:
    """One non-stream generate. Qwen3: think=False turns off reasoning mode (speed).

    max_tokens caps the reply; a structured call (json_schema) defaults to JSON_MAX_TOKENS and
    a reply cut off by the cap raises OllamaError (the JSON would be incomplete anyway).
    repeat_penalty is sent to Ollama only when given (OpenAI-compatible APIs ignore it)."""
    limit = max_tokens if max_tokens is not None else (JSON_MAX_TOKENS if json_schema else None)
    with span(f"generate {model}") as s:
        set_attrs(
            s,
            **_llm_span_attrs("text_completion", model, temperature),
            rag__json_schema=json_schema is not None,
            rag__prompt_chars=len(prompt),
        )
        if _provider() == "openai":
            text, usage = _openai_generate(
                model, prompt, system, json_schema, temperature, timeout, limit
            )
        else:
            text, usage = _ollama_generate(
                model,
                prompt,
                system,
                json_schema,
                temperature,
                num_ctx,
                timeout,
                think,
                limit,
                repeat_penalty,
            )
        record_llm_usage(s, usage)
        if usage.get("done_reason") == "length":
            if json_schema is not None:
                raise OllamaError(f"odpoveď ({model}) narazila na limit {limit} tokenov")
            logger.warning("odpoveď (%s) skrátená na %s tokenov", model, limit)
        set_attrs(s, rag__output_chars=len(text))
        return text


def _ollama_generate(
    model: str,
    prompt: str,
    system: str | None,
    json_schema: dict[str, Any] | None,
    temperature: float,
    num_ctx: int,
    timeout: float,
    think: bool,
    max_tokens: int | None,
    repeat_penalty: float | None = None,
) -> tuple[str, dict[str, Any]]:
    options: dict[str, Any] = {"temperature": temperature, "num_ctx": _num_ctx(num_ctx)}
    if repeat_penalty is not None:
        options["repeat_penalty"] = repeat_penalty
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": think,
        "keep_alive": "5m",
        "options": options,
    }
    if system is not None:
        payload["system"] = system
    if json_schema is not None:
        payload["format"] = json_schema
    try:
        resp = httpx.post(
            f"{get_settings().ollama_base_url}/api/generate", json=payload, timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError: a 200 with a non-JSON body (e.g. an HTML page from a proxy)
        raise OllamaError(f"ollama generate zlyhal ({model}): {exc}") from exc
    if not isinstance(data, dict):
        raise OllamaError(f"ollama vrátil neočakávanú odpoveď: {data!r:.200}")
    response = data.get("response")
    if not isinstance(response, str):
        raise OllamaError(f"ollama vrátil neočakávanú odpoveď: {data!r:.200}")
    return response.strip(), data


def _openai_generate(
    model: str,
    prompt: str,
    system: str | None,
    json_schema: dict[str, Any] | None,
    temperature: float,
    timeout: float,
    max_tokens: int | None,
) -> tuple[str, dict[str, Any]]:
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "schema": json_schema, "strict": False},
        }
    try:
        resp = httpx.post(
            _openai_url("/chat/completions"),
            json=payload,
            headers=_openai_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise OllamaError(f"LLM API generate zlyhal ({model}): {exc}") from exc
    return str(content).strip(), _openai_usage(data)


async def chat_stream(
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.3,
    num_ctx: int = 8192,
    think: bool = False,
    parent: Context | None = None,
) -> AsyncIterator[str]:
    """Streams answer tokens. The span is closed after the last token (or on error);
    `parent` is explicit because the calling async generator has no current span."""
    s = get_tracer().start_span(f"chat {model}", context=parent)
    set_attrs(s, **_llm_span_attrs("chat", model, temperature), rag__messages=len(messages))
    chars = 0
    try:
        stream = (
            _openai_chat_stream(model, messages, temperature)
            if _provider() == "openai"
            else _ollama_chat_stream(model, messages, temperature, num_ctx, think)
        )
        async for chunk in stream:
            if isinstance(chunk, dict):
                record_llm_usage(s, chunk)
                continue
            chars += len(chunk)
            yield chunk
    except Exception as exc:
        s.record_exception(exc)
        raise
    finally:
        set_attrs(s, rag__output_chars=chars)
        s.end()


async def _ollama_chat_stream(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    num_ctx: int,
    think: bool,
) -> AsyncIterator[str | dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "keep_alive": "10m",
        "options": {"temperature": temperature, "num_ctx": _num_ctx(num_ctx)},
    }
    timeout = httpx.Timeout(300.0, connect=5.0)
    try:
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST", f"{get_settings().ollama_base_url}/api/chat", json=payload
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if err := data.get("error"):
                    raise OllamaError(f"ollama chat: {err}")
                chunk = data.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if data.get("done"):
                    yield data  # usage (prompt_eval_count, eval_count)
                    return
    except httpx.HTTPError as exc:
        raise OllamaError(f"ollama chat zlyhal ({model}): {exc}") from exc


async def _openai_chat_stream(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
) -> AsyncIterator[str | dict[str, Any]]:
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    timeout = httpx.Timeout(300.0, connect=10.0)
    try:
        async with (
            httpx.AsyncClient(timeout=timeout) as client,
            client.stream(
                "POST", _openai_url("/chat/completions"), json=payload, headers=_openai_headers()
            ) as resp,
        ):
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                body = line[6:].strip()
                if body == "[DONE]":
                    return
                data = json.loads(body)
                if data.get("usage"):
                    yield _openai_usage(data)
                for choice in data.get("choices") or []:
                    if text := (choice.get("delta") or {}).get("content"):
                        yield text
    except httpx.HTTPError as exc:
        raise OllamaError(f"LLM API chat zlyhal ({model}): {exc}") from exc


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolTurn:
    """One agent turn: answer text or tool calls + a message for the history."""

    content: str
    calls: list[ToolCall] = field(default_factory=list)
    message: dict[str, Any] = field(default_factory=dict)  # provider-native format


def tool_result_message(call: ToolCall, content: str) -> dict[str, Any]:
    if _provider() == "openai":
        return {"role": "tool", "tool_call_id": call.id, "content": content}
    return {"role": "tool", "tool_name": call.name, "content": content}


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def chat_with_tools(
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    temperature: float = 0.1,
) -> ToolTurn:
    with span(f"chat {model}") as s:
        set_attrs(s, **_llm_span_attrs("chat", model, temperature), rag__tools=len(tools))
        timeout = httpx.Timeout(300.0, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if _provider() == "openai":
                    resp = await client.post(
                        _openai_url("/chat/completions"),
                        json={
                            "model": model,
                            "messages": messages,
                            "tools": tools,
                            "temperature": temperature,
                        },
                        headers=_openai_headers(),
                    )
                else:
                    resp = await client.post(
                        f"{get_settings().ollama_base_url}/api/chat",
                        json={
                            "model": model,
                            "messages": messages,
                            "tools": tools,
                            "stream": False,
                            "think": False,
                            "keep_alive": "10m",
                            "options": {"temperature": temperature, "num_ctx": _num_ctx(8192)},
                        },
                    )
                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
        except httpx.HTTPError as exc:
            raise OllamaError(f"agent chat zlyhal: {exc}") from exc
        if err := data.get("error"):
            raise OllamaError(f"agent chat: {err}")

        if _provider() == "openai":
            record_llm_usage(s, _openai_usage(data))
            message = data["choices"][0]["message"]
        else:
            record_llm_usage(s, data)
            message = data.get("message", {})
        calls = [
            ToolCall(
                id=str(c.get("id") or uuid.uuid4().hex),
                name=str(c.get("function", {}).get("name", "")),
                arguments=_parse_args(c.get("function", {}).get("arguments")),
            )
            for c in message.get("tool_calls") or []
        ]
        set_attrs(s, rag__tool_calls=[c.name for c in calls])
        return ToolTurn(
            content=(message.get("content") or "").strip(), calls=calls, message=message
        )

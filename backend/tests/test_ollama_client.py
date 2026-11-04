"""LLM client: Ollama + OpenAI-compatible payloads, streaming, tool calls and error mapping.

All HTTP goes through httpx.MockTransport — no Ollama, no network.
"""

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.llm import ollama_client
from app.llm.ollama_client import (
    OllamaError,
    ToolCall,
    ToolTurn,
    _openai_usage,
    _parse_args,
    chat_stream,
    chat_with_tools,
    generate_sync,
)

Handler = Callable[[httpx.Request], httpx.Response]
_REAL_ASYNC_CLIENT = httpx.AsyncClient
_REAL_CLIENT = httpx.Client


class Recorder:
    """Collects requests seen by the mock transport."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    @property
    def body(self) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.last.content)
        return parsed


@pytest.fixture
def http(monkeypatch: pytest.MonkeyPatch) -> Callable[[Handler], Recorder]:
    """Route both httpx.post (sync) and httpx.AsyncClient (async) to a handler."""

    def install(handler: Handler) -> Recorder:
        rec = Recorder()

        def wrapped(request: httpx.Request) -> httpx.Response:
            rec.requests.append(request)
            return handler(request)

        transport = httpx.MockTransport(wrapped)

        def fake_post(url: str, **kwargs: Any) -> httpx.Response:
            with _REAL_CLIENT(transport=transport) as client:
                return client.post(
                    url,
                    json=kwargs.get("json"),
                    headers=kwargs.get("headers"),
                    timeout=kwargs.get("timeout"),
                )

        def fake_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = transport
            return _REAL_ASYNC_CLIENT(*args, **kwargs)

        monkeypatch.setattr(ollama_client.httpx, "post", fake_post)
        monkeypatch.setattr(ollama_client.httpx, "AsyncClient", fake_async_client)
        return rec

    return install


@pytest.fixture
def openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_LLM_PROVIDER", "openai")
    monkeypatch.setenv("RAG_OPENAI_BASE_URL", "https://llm.example/v1/")  # trailing slash
    monkeypatch.setenv("RAG_OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()


def _ndjson(*objs: dict[str, Any], raw: str = "") -> httpx.Response:
    body = "\n".join(json.dumps(o) for o in objs) + "\n" + raw
    return httpx.Response(200, content=body.encode())


def _sse(*events: dict[str, Any] | str, extra: list[str] | None = None) -> httpx.Response:
    lines: list[str] = list(extra or [])
    for e in events:
        lines.append("data: " + (e if isinstance(e, str) else json.dumps(e)))
        lines.append("")
    return httpx.Response(200, content="\n".join(lines).encode())


async def _collect(gen: Any) -> list[Any]:
    return [x async for x in gen]


# --- small helpers ---------------------------------------------------------------------


def test_openai_usage_maps_fields() -> None:
    data = {
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
        "choices": [{"finish_reason": "length"}],
    }
    assert _openai_usage(data) == {
        "prompt_eval_count": 12,
        "eval_count": 4,
        "done_reason": "length",
    }


def test_openai_usage_tolerates_missing_parts() -> None:
    assert _openai_usage({}) == {
        "prompt_eval_count": None,
        "eval_count": None,
        "done_reason": None,
    }
    assert _openai_usage({"usage": None, "choices": []})["done_reason"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"a": 1}, {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ("", {}),
        (None, {}),
        ("{not json", {}),
        ("[1, 2]", {}),  # valid JSON, but not an object
        ('"text"', {}),
    ],
)
def test_parse_args(raw: Any, expected: dict[str, Any]) -> None:
    assert _parse_args(raw) == expected


# --- generate_sync · ollama ------------------------------------------------------------


def test_generate_ollama_payload_and_strip(http: Callable[[Handler], Recorder]) -> None:
    rec = http(lambda r: httpx.Response(200, json={"response": "  Ahoj svet\n", "done": True}))
    out = generate_sync(
        "qwen3:4b",
        "povedz ahoj",
        system="buď stručný",
        json_schema={"type": "object"},
        temperature=0.0,
        num_ctx=2048,
        think=True,
    )
    assert out == "Ahoj svet"
    assert str(rec.last.url) == "http://localhost:11434/api/generate"
    assert rec.body == {
        "model": "qwen3:4b",
        "prompt": "povedz ahoj",
        "stream": False,
        "think": True,
        "keep_alive": "5m",
        "options": {"temperature": 0.0, "num_ctx": 2048},
        "system": "buď stručný",
        "format": {"type": "object"},
    }


def test_generate_ollama_omits_optional_fields(http: Callable[[Handler], Recorder]) -> None:
    rec = http(lambda r: httpx.Response(200, json={"response": "ok"}))
    generate_sync("m", "p")
    assert "system" not in rec.body
    assert "format" not in rec.body
    assert rec.body["think"] is False  # reasoning off by default


def test_generate_ollama_uses_configured_base_url(
    http: Callable[[Handler], Recorder], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_OLLAMA_BASE_URL", "http://gpu-box:11434")
    get_settings.cache_clear()
    rec = http(lambda r: httpx.Response(200, json={"response": "ok"}))
    generate_sync("m", "p")
    assert str(rec.last.url) == "http://gpu-box:11434/api/generate"


@pytest.mark.parametrize("status", [404, 500, 503])
def test_generate_ollama_http_status_becomes_ollama_error(
    http: Callable[[Handler], Recorder], status: int
) -> None:
    http(lambda r: httpx.Response(status, json={"error": "boom"}))
    with pytest.raises(OllamaError, match="ollama generate zlyhal"):
        generate_sync("m", "p")


def test_generate_ollama_timeout_becomes_ollama_error(
    http: Callable[[Handler], Recorder],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow model", request=request)

    http(handler)
    with pytest.raises(OllamaError, match="ollama generate zlyhal \\(slow\\)"):
        generate_sync("slow", "p", timeout=0.1)


def test_generate_ollama_connection_refused_becomes_ollama_error(
    http: Callable[[Handler], Recorder],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    http(handler)
    with pytest.raises(OllamaError):
        generate_sync("m", "p")


@pytest.mark.parametrize("body", [{"response": None}, {"response": 42}, {"error": "model missing"}])
def test_generate_ollama_unexpected_body_is_error(
    http: Callable[[Handler], Recorder], body: dict[str, Any]
) -> None:
    http(lambda r: httpx.Response(200, json=body))
    with pytest.raises(OllamaError, match="neočakávanú odpoveď"):
        generate_sync("m", "p")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: a 200 response with a non-JSON body (e.g. an HTML page from a reverse proxy) "
        "raises json.JSONDecodeError instead of OllamaError, so callers that only catch "
        "OllamaError (chunker, router, memory) crash. _openai_generate already catches ValueError."
    ),
)
def test_generate_ollama_non_json_body_is_ollama_error(
    http: Callable[[Handler], Recorder],
) -> None:
    http(lambda r: httpx.Response(200, content=b"<html>502 Bad Gateway</html>"))
    with pytest.raises(OllamaError):
        generate_sync("m", "p")


# --- generate_sync · openai ------------------------------------------------------------


def test_generate_openai_without_system_or_schema(
    http: Callable[[Handler], Recorder], openai: None
) -> None:
    rec = http(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": " hotovo "}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
        )
    )
    assert generate_sync("gpt-x", "ahoj") == "hotovo"
    assert str(rec.last.url) == "https://llm.example/v1/chat/completions"  # no double slash
    assert rec.body["messages"] == [{"role": "user", "content": "ahoj"}]
    assert "response_format" not in rec.body
    assert rec.body["temperature"] == 0.2
    assert rec.last.headers["authorization"] == "Bearer sk-test"


def test_generate_openai_without_api_key_sends_no_auth_header(
    http: Callable[[Handler], Recorder], openai: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAG_OPENAI_API_KEY")
    get_settings.cache_clear()
    rec = http(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]}))
    generate_sync("m", "p")  # local servers (LM Studio, vLLM) need no key
    assert "authorization" not in rec.last.headers


def test_generate_openai_null_content_is_empty_string(
    http: Callable[[Handler], Recorder], openai: None
) -> None:
    http(lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None}}]}))
    assert generate_sync("m", "p") == ""


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": {"message": "bad key"}}),
        httpx.Response(429, json={"error": "rate limit"}),
        httpx.Response(200, json={"choices": []}),  # IndexError
        httpx.Response(200, json={"id": "x"}),  # KeyError
        httpx.Response(200, content=b"not json"),  # ValueError
    ],
)
def test_generate_openai_failures_become_ollama_error(
    http: Callable[[Handler], Recorder], openai: None, response: httpx.Response
) -> None:
    http(lambda r: response)
    with pytest.raises(OllamaError, match="LLM API generate zlyhal"):
        generate_sync("m", "p")


# --- chat_stream · ollama --------------------------------------------------------------


@pytest.mark.anyio
async def test_chat_stream_ollama_yields_text_and_skips_usage(
    http: Callable[[Handler], Recorder],
) -> None:
    rec = http(
        lambda r: _ndjson(
            {"message": {"content": "Ahoj"}, "done": False},
            {"message": {"content": ""}, "done": False},  # empty chunk is dropped
            {"message": {"content": " svet"}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 3, "prompt_eval_count": 7},
            raw="\n",  # blank trailing line must not break parsing
        )
    )
    chunks = await _collect(chat_stream("m", [{"role": "user", "content": "hi"}]))
    assert chunks == ["Ahoj", " svet"]  # usage dict is consumed internally, never yielded
    assert rec.body["stream"] is True
    assert rec.body["options"] == {"temperature": 0.3, "num_ctx": 8192}
    assert str(rec.last.url).endswith("/api/chat")


@pytest.mark.anyio
async def test_chat_stream_ollama_ignores_blank_lines_between_chunks(
    http: Callable[[Handler], Recorder],
) -> None:
    body = (
        json.dumps({"message": {"content": "A"}, "done": False})
        + "\n\n   \n"
        + json.dumps({"message": {"content": "B"}, "done": True})
        + "\n"
    )
    http(lambda r: httpx.Response(200, content=body.encode()))
    assert await _collect(chat_stream("m", [])) == ["A", "B"]


@pytest.mark.anyio
async def test_chat_stream_ollama_stops_at_done(http: Callable[[Handler], Recorder]) -> None:
    http(
        lambda r: _ndjson(
            {"message": {"content": "A"}, "done": True},
            {"message": {"content": "NEMALO BY PRIJSŤ"}, "done": False},
        )
    )
    assert await _collect(chat_stream("m", [])) == ["A"]


@pytest.mark.anyio
async def test_chat_stream_ollama_error_line_raises(http: Callable[[Handler], Recorder]) -> None:
    http(
        lambda r: _ndjson(
            {"message": {"content": "pol"}, "done": False},
            {"error": "model 'x' not found"},
        )
    )
    seen: list[str] = []
    with pytest.raises(OllamaError, match="model 'x' not found"):
        async for chunk in chat_stream("x", []):
            seen.append(chunk)
    assert seen == ["pol"]  # tokens before the error were already delivered


@pytest.mark.anyio
async def test_chat_stream_ollama_http_error(http: Callable[[Handler], Recorder]) -> None:
    http(lambda r: httpx.Response(500, content=b"internal"))
    with pytest.raises(OllamaError, match="ollama chat zlyhal"):
        await _collect(chat_stream("m", []))


@pytest.mark.anyio
async def test_chat_stream_ollama_connect_error(http: Callable[[Handler], Recorder]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    http(handler)
    with pytest.raises(OllamaError, match="ollama chat zlyhal"):
        await _collect(chat_stream("m", []))


# --- chat_stream · openai --------------------------------------------------------------


@pytest.mark.anyio
async def test_chat_stream_openai_sse(http: Callable[[Handler], Recorder], openai: None) -> None:
    rec = http(
        lambda r: _sse(
            {"choices": [{"delta": {"role": "assistant"}}]},  # no content
            {"choices": [{"delta": {"content": "Ahoj"}}]},
            {"choices": []},  # usage-only frames have no choices
            {"choices": [{"delta": {"content": " svet"}}]},
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
            "[DONE]",
            {"choices": [{"delta": {"content": "PO DONE"}}]},
            extra=[": keep-alive comment", "event: ping"],
        )
    )
    chunks = await _collect(chat_stream("gpt-x", [{"role": "user", "content": "hi"}]))
    assert chunks == ["Ahoj", " svet"]
    assert rec.body["stream"] is True
    assert rec.body["stream_options"] == {"include_usage": True}
    assert rec.last.headers["authorization"] == "Bearer sk-test"
    assert str(rec.last.url) == "https://llm.example/v1/chat/completions"


@pytest.mark.anyio
async def test_chat_stream_openai_http_error(
    http: Callable[[Handler], Recorder], openai: None
) -> None:
    http(lambda r: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(OllamaError, match="LLM API chat zlyhal"):
        await _collect(chat_stream("m", []))


# --- chat_with_tools -------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "search_docs"}}]


@pytest.mark.anyio
async def test_chat_with_tools_ollama_tool_calls(http: Callable[[Handler], Recorder]) -> None:
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"function": {"name": "search_docs", "arguments": {"query": "pokuta"}}},
            {"id": "abc", "function": {"name": "pick_items", "arguments": {"budget_max": 5}}},
        ],
    }
    rec = http(lambda r: httpx.Response(200, json={"message": message, "eval_count": 4}))
    turn = await chat_with_tools("m", [{"role": "user", "content": "q"}], TOOLS)

    assert [c.name for c in turn.calls] == ["search_docs", "pick_items"]
    assert turn.calls[0].arguments == {"query": "pokuta"}
    assert turn.calls[1].id == "abc"
    assert turn.calls[0].id and turn.calls[0].id != turn.calls[1].id  # generated when missing
    assert turn.message == message  # native message is kept for the next round
    assert rec.body["tools"] == TOOLS
    assert rec.body["stream"] is False
    assert rec.body["options"] == {"temperature": 0.1, "num_ctx": 8192}


@pytest.mark.anyio
async def test_chat_with_tools_plain_answer(http: Callable[[Handler], Recorder]) -> None:
    http(lambda r: httpx.Response(200, json={"message": {"content": "  Odpoveď  "}}))
    turn = await chat_with_tools("m", [], TOOLS)
    assert turn == ToolTurn(content="Odpoveď", calls=[], message={"content": "  Odpoveď  "})


@pytest.mark.anyio
async def test_chat_with_tools_handles_null_content_and_missing_message(
    http: Callable[[Handler], Recorder],
) -> None:
    http(lambda r: httpx.Response(200, json={"message": {"content": None, "tool_calls": None}}))
    assert (await chat_with_tools("m", [], TOOLS)).content == ""
    http(lambda r: httpx.Response(200, json={}))
    empty = await chat_with_tools("m", [], TOOLS)
    assert empty.content == "" and empty.calls == []


@pytest.mark.anyio
async def test_chat_with_tools_error_field_raises(http: Callable[[Handler], Recorder]) -> None:
    http(lambda r: httpx.Response(200, json={"error": "this model does not support tools"}))
    with pytest.raises(OllamaError, match="does not support tools"):
        await chat_with_tools("m", [], TOOLS)


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 500])
async def test_chat_with_tools_http_error(http: Callable[[Handler], Recorder], status: int) -> None:
    http(lambda r: httpx.Response(status, json={"error": "x"}))
    with pytest.raises(OllamaError, match="agent chat zlyhal"):
        await chat_with_tools("m", [], TOOLS)


@pytest.mark.anyio
async def test_chat_with_tools_timeout(http: Callable[[Handler], Recorder]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    http(handler)
    with pytest.raises(OllamaError, match="agent chat zlyhal"):
        await chat_with_tools("m", [], TOOLS)


@pytest.mark.anyio
async def test_chat_with_tools_openai_parses_json_string_arguments(
    http: Callable[[Handler], Recorder], openai: None
) -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "query_records", "arguments": '{"constraints": []}'},
            },
            {"id": "call_2", "function": {"name": "search_docs", "arguments": "{broken"}},
            {"id": "call_3", "function": {"name": "pick_items", "arguments": "[1]"}},
        ],
    }
    rec = http(
        lambda r: httpx.Response(
            200,
            json={
                "choices": [{"message": message, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )
    )
    turn = await chat_with_tools("gpt-x", [{"role": "user", "content": "q"}], TOOLS)
    assert [(c.id, c.name) for c in turn.calls] == [
        ("call_1", "query_records"),
        ("call_2", "search_docs"),
        ("call_3", "pick_items"),
    ]
    assert turn.calls[0].arguments == {"constraints": []}
    assert turn.calls[1].arguments == {}  # malformed JSON from the model → empty args, no crash
    assert turn.calls[2].arguments == {}  # JSON but not an object
    assert turn.content == ""
    assert rec.body["tools"] == TOOLS
    assert "stream" not in rec.body
    assert rec.last.headers["authorization"] == "Bearer sk-test"


@pytest.mark.anyio
async def test_chat_with_tools_openai_error_body_raises(
    http: Callable[[Handler], Recorder], openai: None
) -> None:
    http(lambda r: httpx.Response(200, json={"error": {"message": "quota exceeded"}}))
    with pytest.raises(OllamaError, match="quota exceeded"):
        await chat_with_tools("m", [], TOOLS)


def test_tool_call_dataclass_roundtrip() -> None:
    call = ToolCall(id="1", name="n", arguments={"a": 1})
    assert call.arguments == {"a": 1}


@pytest.fixture(autouse=True)
def _reset_settings() -> Iterator[None]:
    yield
    get_settings.cache_clear()

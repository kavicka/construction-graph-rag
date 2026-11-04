import json
from typing import Any

import httpx
import pytest

from app.config import get_settings
from app.retrieve import memory
from app.retrieve.fuse import rrf_fuse
from app.retrieve.memory import needs_rewrite, rewrite_question
from app.retrieve.rerank import rerank_rows


def _row(cid: str) -> dict[str, Any]:
    return {"chunk_id": cid, "text": f"text {cid}"}


def test_weighted_rrf_downweights_list() -> None:
    a = [_row("x"), _row("y")]
    b = [_row("z"), _row("y")]
    assert rrf_fuse([a, b], top_k=1)[0]["chunk_id"] == "y"  # in both lists
    # weak second list: z (1st in b) drops behind x (1st in a); without weights it would tie with it
    top = rrf_fuse([a, b], top_k=3, weights=[1.0, 0.1])
    assert [r["chunk_id"] for r in top] == ["y", "x", "z"]


def test_rerank_rows_orders_by_cross_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        model_name = "fake"

        def score(self, query: str, passages: list[str]) -> list[float]:
            return [float(p.endswith("c")) for p in passages]

    monkeypatch.setattr("app.retrieve.rerank.get_reranker", lambda: Fake())
    out = rerank_rows("q", [_row("a"), _row("b"), _row("c")], top_k=2)
    assert out[0]["chunk_id"] == "c"
    assert out[0]["rerank_score"] == 1.0
    assert len(out) == 2


def test_effective_rerank_follows_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAG_RERANK", raising=False)
    monkeypatch.setenv("RAG_RAM_PROFILE", "low")
    get_settings.cache_clear()
    assert get_settings().effective_rerank is False
    monkeypatch.setenv("RAG_RAM_PROFILE", "high")
    get_settings.cache_clear()
    assert get_settings().effective_rerank is True
    assert get_settings().effective_reranker_model == "BAAI/bge-reranker-v2-m3"


def test_low_reranker_is_permissive_and_routed_by_fastembed_support() -> None:
    from app.config import PROFILE_RERANKER_MODELS
    from app.retrieve.rerank import _fastembed_supports

    # CC-BY-NC (jina-v2) must not be the default — the repo is MIT
    assert PROFILE_RERANKER_MODELS["low"] == "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    assert not _fastembed_supports(PROFILE_RERANKER_MODELS["low"])  # → sentence-transformers
    assert _fastembed_supports("Xenova/ms-marco-MiniLM-L-6-v2")


HISTORY = [
    {"role": "user", "content": "Aká je cena diela Agátová?"},
    {"role": "assistant", "content": "Cena je 4 860 000 EUR [1]."},
]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("A aká je tam pokuta?", True),
        ("A Senec?", True),  # short question
        ("What about the warranty?", True),
        ("Aký je termín dokončenia haly Senec podľa zmluvy ZoD-2026/021?", False),
    ],
)
def test_needs_rewrite(question: str, expected: bool) -> None:
    assert needs_rewrite(question, HISTORY) is expected
    assert needs_rewrite(question, []) is False


def test_rewrite_question_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        memory,
        "generate_sync",
        lambda *a, **k: '{"refers_to": "Agátová", "question": "Aká je zmluvná pokuta Agátová?"}',
    )
    assert rewrite_question("A pokuta?", HISTORY) == "Aká je zmluvná pokuta Agátová?"
    # model answered instead of rewriting / failed / left the question unchanged → join
    # with the previous question so retrieval knows what is being discussed
    fallback = "Aká je cena diela Agátová? A pokuta?"
    monkeypatch.setattr(
        memory,
        "generate_sync",
        lambda *a, **k: json.dumps({"refers_to": "", "question": "x" * 400}),
    )
    assert rewrite_question("A pokuta?", HISTORY) == fallback
    monkeypatch.setattr(memory, "generate_sync", lambda *a, **k: "nie json")
    assert rewrite_question("A pokuta?", HISTORY) == fallback
    monkeypatch.setattr(
        memory, "generate_sync", lambda *a, **k: '{"refers_to": "", "question": "A pokuta ?"}'
    )
    assert rewrite_question("A pokuta?", HISTORY) == fallback


def test_openai_compatible_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAG_LLM_PROVIDER=openai: chat/completions, json_schema response_format, usage in the span."""
    from app.llm import ollama_client

    seen: dict[str, Any] = {}

    def fake_post(
        url: str, json: dict[str, Any], headers: dict[str, str], timeout: float
    ) -> httpx.Response:
        seen.update(url=url, body=json, headers=headers)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": ' {"ok": true} '}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3},
            },
            request=httpx.Request("POST", url),
        )

    monkeypatch.setenv("RAG_LLM_PROVIDER", "openai")
    monkeypatch.setenv("RAG_OPENAI_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("RAG_OPENAI_API_KEY", "test-key")
    get_settings.cache_clear()
    monkeypatch.setattr(ollama_client.httpx, "post", fake_post)

    out = ollama_client.generate_sync(
        "gpt-test", "ahoj", system="sys", json_schema={"type": "object"}
    )
    assert out == '{"ok": true}'
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["response_format"]["json_schema"]["schema"] == {"type": "object"}


def test_tool_result_message_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm.ollama_client import ToolCall, tool_result_message

    call = ToolCall(id="call_1", name="search_docs", arguments={})
    assert tool_result_message(call, "x") == {
        "role": "tool",
        "tool_name": "search_docs",
        "content": "x",
    }
    monkeypatch.setenv("RAG_LLM_PROVIDER", "openai")
    get_settings.cache_clear()
    assert tool_result_message(call, "x") == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "x",
    }

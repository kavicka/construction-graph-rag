"""Cross-encoder reranking: ordering, top-k, passage truncation and model routing.

The heavy backends (fastembed ONNX, torch + sentence-transformers) are replaced with
fakes, so no model is downloaded or loaded.
"""

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from app.config import get_settings
from app.retrieve import rerank
from app.retrieve.rerank import (
    MAX_PASSAGE_CHARS,
    FastembedReranker,
    SentenceTransformersReranker,
    get_reranker,
    rerank_rows,
)


class RecordingReranker:
    """Scores a passage by a lookup table; remembers what it was asked."""

    model_name = "recording"

    def __init__(self, scores: dict[str, float] | None = None) -> None:
        self.scores = scores or {}
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, passages))
        return [self.scores.get(p, 0.0) for p in passages]


def _row(cid: str, text: str | None = None) -> dict[str, Any]:
    return {"chunk_id": cid, "text": text if text is not None else f"text {cid}"}


@pytest.fixture
def reranker(monkeypatch: pytest.MonkeyPatch) -> RecordingReranker:
    fake = RecordingReranker()
    monkeypatch.setattr(rerank, "get_reranker", lambda: fake)
    return fake


# --- rerank_rows -----------------------------------------------------------------------


def test_orders_by_descending_score(reranker: RecordingReranker) -> None:
    reranker.scores = {"text a": 0.1, "text b": 0.9, "text c": 0.5}
    out = rerank_rows("q", [_row("a"), _row("b"), _row("c")], top_k=3)
    assert [r["chunk_id"] for r in out] == ["b", "c", "a"]
    assert [r["rerank_score"] for r in out] == [0.9, 0.5, 0.1]


def test_negative_scores_still_sort_correctly(reranker: RecordingReranker) -> None:
    # cross-encoders emit raw logits, which are often negative
    reranker.scores = {"text a": -7.2, "text b": -0.4, "text c": -3.0}
    out = rerank_rows("q", [_row("a"), _row("b"), _row("c")], top_k=2)
    assert [r["chunk_id"] for r in out] == ["b", "c"]


def test_top_k_truncates_after_sorting(reranker: RecordingReranker) -> None:
    reranker.scores = {"text d": 5.0, "text a": 1.0}
    rows = [_row(c) for c in "abcd"]
    out = rerank_rows("q", rows, top_k=2)
    assert [r["chunk_id"] for r in out] == ["d", "a"]  # best of ALL rows, not of the first two


def test_top_k_larger_than_rows_returns_everything(reranker: RecordingReranker) -> None:
    out = rerank_rows("q", [_row("a"), _row("b")], top_k=50)
    assert len(out) == 2


def test_top_k_zero_returns_nothing(reranker: RecordingReranker) -> None:
    assert rerank_rows("q", [_row("a"), _row("b")], top_k=0) == []


def test_ties_keep_original_order(reranker: RecordingReranker) -> None:
    # fusion order is the tie-breaker when the cross-encoder cannot tell candidates apart
    out = rerank_rows("q", [_row("a"), _row("b"), _row("c")], top_k=3)
    assert [r["chunk_id"] for r in out] == ["a", "b", "c"]


def test_input_rows_are_not_mutated(reranker: RecordingReranker) -> None:
    rows = [_row("a"), _row("b")]
    out = rerank_rows("q", rows, top_k=2)
    assert all("rerank_score" not in r for r in rows)
    assert all(o is not r for o, r in zip(out, rows, strict=True))
    assert out[0]["text"] == "text a"  # other fields are carried over


def test_query_is_passed_through(reranker: RecordingReranker) -> None:
    rerank_rows("zmluvná pokuta", [_row("a"), _row("b")], top_k=2)
    assert reranker.calls[0][0] == "zmluvná pokuta"


def test_long_passages_are_truncated_for_the_model(reranker: RecordingReranker) -> None:
    long_text = "x" * (MAX_PASSAGE_CHARS * 3)
    out = rerank_rows("q", [_row("a", long_text), _row("b", "short")], top_k=2)
    passages = reranker.calls[0][1]
    assert [len(p) for p in passages] == [MAX_PASSAGE_CHARS, 5]
    assert next(r for r in out if r["chunk_id"] == "a")["text"] == long_text  # row keeps full text


def test_empty_input_does_not_touch_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise AssertionError("model must not be loaded for <= 1 candidate")

    monkeypatch.setattr(rerank, "get_reranker", boom)
    assert rerank_rows("q", [], top_k=5) == []


def test_single_row_is_returned_without_scoring(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise AssertionError("model must not be loaded for <= 1 candidate")

    monkeypatch.setattr(rerank, "get_reranker", boom)
    row = _row("a")
    assert rerank_rows("q", [row], top_k=5) == [row]
    assert rerank_rows("q", [row], top_k=0) == []


def test_mismatched_score_count_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        model_name = "broken"

        def score(self, query: str, passages: list[str]) -> list[float]:
            return [1.0]  # one score for two passages

    monkeypatch.setattr(rerank, "get_reranker", lambda: Broken())
    with pytest.raises(ValueError):
        rerank_rows("q", [_row("a"), _row("b")], top_k=2)


# --- backends --------------------------------------------------------------------------


def test_fastembed_reranker_converts_scores_to_float(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class FakeCrossEncoder:
        def __init__(self, model_name: str) -> None:
            seen["model"] = model_name

        def rerank(self, query: str, passages: list[str], batch_size: int) -> list[Any]:
            seen.update(query=query, passages=passages, batch_size=batch_size)
            return [1, 3, 2]  # ints stand in for numpy scalars

    monkeypatch.setattr("fastembed.rerank.cross_encoder.TextCrossEncoder", FakeCrossEncoder)
    r = FastembedReranker("some/model")
    scores = r.score("q", ["a", "b", "c"])
    assert r.model_name == "some/model"
    assert seen == {
        "model": "some/model",
        "query": "q",
        "passages": ["a", "b", "c"],
        "batch_size": 16,
    }
    assert scores == [1.0, 3.0, 2.0]
    assert all(type(s) is float for s in scores)


def _install_fake_torch_and_st(
    monkeypatch: pytest.MonkeyPatch, *, mps: bool, seen: dict[str, Any]
) -> None:
    class FakeModelInner:
        def half(self) -> None:
            seen["half"] = True

    class FakeCrossEncoder:
        def __init__(self, name: str, device: str, max_length: int) -> None:
            seen.update(name=name, device=device, max_length=max_length)
            self.model = FakeModelInner()

        def predict(
            self, pairs: list[tuple[str, str]], batch_size: int, show_progress_bar: bool
        ) -> list[Any]:
            seen.update(pairs=pairs, batch_size=batch_size, progress=show_progress_bar)
            return [0.25, -1]

    torch = ModuleType("torch")
    torch.backends = SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps))  # type: ignore[attr-defined]
    st = ModuleType("sentence_transformers")
    st.CrossEncoder = FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", st)


def test_sentence_transformers_reranker_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _install_fake_torch_and_st(monkeypatch, mps=False, seen=seen)
    r = SentenceTransformersReranker("cross/enc")
    scores = r.score("otázka", ["p1", "p2"])
    assert seen["device"] == "cpu"
    assert "half" not in seen  # fp16 only on Apple GPU
    assert seen["max_length"] == 384
    assert seen["pairs"] == [("otázka", "p1"), ("otázka", "p2")]
    assert seen["progress"] is False
    assert scores == [0.25, -1.0]
    assert all(type(s) is float for s in scores)


def test_sentence_transformers_reranker_uses_fp16_on_mps(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    _install_fake_torch_and_st(monkeypatch, mps=True, seen=seen)
    SentenceTransformersReranker("cross/enc")
    assert seen["device"] == "mps"
    assert seen["half"] is True


# --- model routing ---------------------------------------------------------------------


@pytest.fixture
def fresh_reranker_cache() -> Any:
    get_reranker.cache_clear()
    yield
    get_reranker.cache_clear()


def test_get_reranker_prefers_fastembed_when_supported(
    monkeypatch: pytest.MonkeyPatch, fresh_reranker_cache: None
) -> None:
    monkeypatch.setenv("RAG_RERANKER_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
    get_settings.cache_clear()
    created: list[tuple[str, str]] = []
    monkeypatch.setattr(rerank, "_fastembed_supports", lambda m: True)
    monkeypatch.setattr(
        rerank, "FastembedReranker", lambda m: created.append(("fastembed", m)) or object()
    )
    monkeypatch.setattr(
        rerank, "SentenceTransformersReranker", lambda m: created.append(("st", m)) or object()
    )
    get_reranker()
    assert created == [("fastembed", "Xenova/ms-marco-MiniLM-L-6-v2")]


def test_get_reranker_falls_back_to_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch, fresh_reranker_cache: None
) -> None:
    monkeypatch.setenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    get_settings.cache_clear()
    created: list[tuple[str, str]] = []
    monkeypatch.setattr(rerank, "_fastembed_supports", lambda m: False)
    monkeypatch.setattr(
        rerank, "FastembedReranker", lambda m: created.append(("fastembed", m)) or object()
    )
    monkeypatch.setattr(
        rerank, "SentenceTransformersReranker", lambda m: created.append(("st", m)) or object()
    )
    get_reranker()
    assert created == [("st", "BAAI/bge-reranker-v2-m3")]


def test_get_reranker_is_cached(
    monkeypatch: pytest.MonkeyPatch, fresh_reranker_cache: None
) -> None:
    created: list[str] = []
    monkeypatch.setattr(rerank, "_fastembed_supports", lambda m: True)
    monkeypatch.setattr(rerank, "FastembedReranker", lambda m: created.append(m) or object())
    first = get_reranker()
    assert get_reranker() is first
    assert len(created) == 1

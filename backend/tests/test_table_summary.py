import pytest

from app.index.chunker import _fallback_summary, summarize_table
from app.llm.ollama_client import OllamaError

MD = "| Vzorka | Prietok (l/min) |\n|---|---|\n| A-01 | 12.4 |"


def test_fallback_summary_contains_columns() -> None:
    s = _fallback_summary("Tab. 1", MD, 2, 2)
    assert "Tab. 1" in s
    assert "Prietok" in s
    assert "2×2" in s


def test_summarize_uses_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.index.chunker.generate_sync", lambda *a, **kw: "Tabuľka meraní prietoku."
    )
    assert summarize_table("Tab. 1", MD, 2, 2) == "Tabuľka meraní prietoku."


def test_summarize_falls_back_when_ollama_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **kw: object) -> str:
        raise OllamaError("down")

    monkeypatch.setattr("app.index.chunker.generate_sync", boom)
    s = summarize_table("Tab. 1", MD, 2, 2)
    assert "Prietok" in s  # deterministický fallback, ingest nesmie spadnúť

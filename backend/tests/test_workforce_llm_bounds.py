"""Runaway LLM replies and task-title similarity on the small embedding model.

No Ollama and no embedding model: generate_sync and get_embedder are faked.
"""

from typing import Any

import pytest

from app.config import get_settings
from app.llm.ollama_client import JSON_MAX_TOKENS, OllamaError
from app.workforce import extract, importer
from app.workforce.extract import _Extracted
from app.workforce.importer import (
    IMPORT_MAX_TOKENS,
    MATCH_SCORE,
    Draft,
    Similarity,
    _XDocument,
    flag_existing,
)
from app.workforce.report_model import ReportData, find_gaps

TASKS = [{"id": "t1", "title": "Murovanie priečok", "description": "", "site_name": "Agátová"}]


def _props(schema: dict[str, Any], name: str) -> dict[str, Any]:
    props: dict[str, Any] = schema["$defs"][name]["properties"] if name else schema["properties"]
    return props


# --- 1. bounded LLM output -----------------------------------------------------------------


def test_report_schema_bounds_free_text() -> None:
    schema = _Extracted.model_json_schema()
    top = _props(schema, "")
    assert top["summary"]["maxLength"] == 500
    assert top["absence_reason"]["maxLength"] == 300
    assert top["problems"]["items"]["maxLength"] == 300
    assert top["problems"]["maxItems"] == 10
    assert top["tasks"]["maxItems"] == 30
    assert _props(schema, "_XTask")["note"]["maxLength"] == 300
    assert _props(schema, "_XExtra")["description"]["maxLength"] == 300


def test_import_schema_bounds_free_text() -> None:
    schema = _XDocument.model_json_schema()
    task = _props(schema, "_XTask")
    assert task["quote"]["maxLength"] == 500
    assert task["description"]["maxLength"] == 500
    assert {"maxLength": 300, "type": "string"} in task["after"]["anyOf"]
    assert _props(schema, "")["tasks"]["maxItems"] == 40


def test_bounds_are_schema_only_not_validation() -> None:
    """A provider without a grammar may exceed them — the reply must still parse."""
    long = "a" * 2000
    parsed = _Extracted.model_validate({"summary": long, "problems": [long] * 20})
    assert len(parsed.summary) == 2000
    assert len(parsed.problems) == 20


def test_llm_json_passes_token_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake(model: str, prompt: str, **kw: Any) -> str:
        seen.append(kw)
        return "{}"

    monkeypatch.setattr(extract, "generate_sync", fake)
    extract._llm_json("p", "s", _Extracted)
    extract._llm_json("p", "s", _XDocument, max_tokens=IMPORT_MAX_TOKENS)
    assert [kw["max_tokens"] for kw in seen] == [JSON_MAX_TOKENS, IMPORT_MAX_TOKENS]
    assert seen[0]["json_schema"]["properties"]["summary"]["maxLength"] == 500
    # the repeat penalty is applied to these extraction calls only, not globally
    assert {kw["repeat_penalty"] for kw in seen} == {extract.REPEAT_PENALTY}


def test_cut_off_extraction_falls_back_to_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reply that hit the cap (OllamaError) leaves the deterministic rules + follow-ups."""

    def capped(*_a: Any, **_k: Any) -> str:
        raise OllamaError("odpoveď (m) narazila na limit 1024 tokenov")

    monkeypatch.setattr(extract, "generate_sync", capped)
    data = extract.extract_report(
        "Prišiel som o siedmej, odišiel som o pol štvrtej.", TASKS, ReportData(), "2026-11-05"
    )
    assert (data.arrival, data.departure) == ("07:00", "15:30")
    gaps = {g.kind for g in find_gaps(data, ["t1"], tolerance=0.5)}
    assert {"break", "task", "problems"} <= gaps


# --- 2. duplicate threshold by embedding model ---------------------------------------------


class _FakeEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls = 0

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self.vectors.get(t, [1.0, 0.0]) for t in texts]


UNRELATED = ["Betonáž stropu nad 4. NP", "Skúška pevnosti betónu stropu nad 2. NP"]


def _use_model(monkeypatch: pytest.MonkeyPatch, model: str, fake: _FakeEmbedder) -> None:
    monkeypatch.setenv("RAG_EMBEDDING_MODEL", model)
    get_settings.cache_clear()
    monkeypatch.setattr("app.index.embedder.get_embedder", lambda: fake)


def test_e5_small_cosine_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # e5-small puts these two at ~0.94 — here even an identical vector must not count
    fake = _FakeEmbedder({t: [1.0, 0.0] for t in UNRELATED})
    _use_model(monkeypatch, "intfloat/multilingual-e5-small", fake)
    sim = Similarity(UNRELATED)
    assert fake.calls == 0
    assert sim.score(0, 1) < MATCH_SCORE


def test_bge_m3_cosine_still_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    synonyms = ["Hydroizolácia šachty", "Utesnenie výťahovej jamy proti vode"]
    fake = _FakeEmbedder({synonyms[0]: [1.0, 0.0], synonyms[1]: [1.0, 0.0]})
    _use_model(monkeypatch, "BAAI/bge-m3", fake)
    sim = Similarity(synonyms)
    assert fake.calls == 1
    assert sim.score(0, 1) == pytest.approx(1.0)


def test_different_numbers_are_different_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lexically ~0.95 (token_set on stems), but floor 4 ≠ floor 2 — on any model."""
    _use_model(monkeypatch, "BAAI/bge-m3", _FakeEmbedder({}))
    sim = Similarity([*UNRELATED, "Betonáž stropu nad 4. NP a 5. NP"])
    assert sim.score(0, 1) < MATCH_SCORE
    assert sim.score(0, 2) >= MATCH_SCORE  # shared number → still comparable


def _flag(titles: list[str], existing: list[str]) -> list[str | None]:
    drafts = [
        Draft({"title": t, "date_from": "2026-11-05", "date_to": "2026-11-09"}) for t in titles
    ]
    tasks = [{"id": f"x{i}", "title": t, "status": "todo"} for i, t in enumerate(existing)]
    flag_existing(drafts, tasks)
    return [(d.data.get("duplicate_of") or {}).get("title") for d in drafts]


def test_flag_existing_on_e5_keeps_real_duplicates_only(monkeypatch: pytest.MonkeyPatch) -> None:
    everything_similar = _FakeEmbedder({})  # every title gets the same vector
    _use_model(monkeypatch, "intfloat/multilingual-e5-small", everything_similar)
    flags = _flag(
        ["Betonáž stropu nad 4. NP", "Doplnenie hydroizolácie výťahovej šachte"],
        ["Skúška pevnosti betónu stropu nad 2. NP", "Doplnenie hydroizolácie pri výťahovej šachte"],
    )
    assert flags == [None, "Doplnenie hydroizolácie pri výťahovej šachte"]


def test_unknown_model_keeps_cosine(monkeypatch: pytest.MonkeyPatch) -> None:
    assert importer._cosine_usable("some/new-model")
    assert not importer._cosine_usable("intfloat/multilingual-e5-small")

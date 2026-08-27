import pytest

from app.graph.extract import (
    extract_concepts_llm,
    extract_quantities,
    extract_references,
    normalize_key,
)
from app.llm.ollama_client import OllamaError


def test_normalize_key_diacritics_and_case() -> None:
    assert normalize_key("Chladiace Médium") == "chladiace-medium"
    assert normalize_key("  X-ray   difrakcia ") == "x-ray-difrakcia"
    assert normalize_key("Å ngström") == normalize_key("a ngstrom")


def test_extract_quantities() -> None:
    text = "Prietok 12.4 l/min pri tlaku 101,3 kPa a teplote 21.5 °C. Opäť 12.4 l/min."
    quantities = extract_quantities(text)
    labels = {q[1] for q in quantities}
    assert "12.4 l/min" in labels
    assert "101,3 kPa" in labels
    assert "21.5 °C" in labels
    assert len(quantities) == 3  # duplicitný výskyt sa nezdvojí


def test_extract_references() -> None:
    text = (
        "Podľa doi:10.1038/s41586-020-2649-2 a arXiv:2103.14030. "
        "Norma STN EN 12345:2019 platí. Pozri ISO 9001."
    )
    refs = extract_references(text)
    types = {r[2] for r in refs}
    labels = {r[1] for r in refs}
    assert types == {"doi", "arxiv", "norm"}
    assert "10.1038/s41586-020-2649-2" in labels
    assert "arXiv:2103.14030" in labels


def test_llm_concepts_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = '{"concepts": [{"name": "röntgenová difrakcia", "type": "method", "defined_here": true}]}'
    monkeypatch.setattr("app.graph.extract.generate_sync", lambda *a, **kw: raw)
    concepts = extract_concepts_llm("text")
    assert concepts[0].name == "röntgenová difrakcia"
    assert concepts[0].type == "method"


def test_llm_concepts_failure_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def boom(*a: object, **kw: object) -> str:
        calls["n"] += 1
        raise OllamaError("down")

    monkeypatch.setattr("app.graph.extract.generate_sync", boom)
    assert extract_concepts_llm("text") == []
    assert calls["n"] == 2  # jeden retry

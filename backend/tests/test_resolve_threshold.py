"""SAME_AS resolution uses a per-model cosine floor.

multilingual-e5-small puts any two short labels at ~0.8 cosine, so with the bge-m3 floor
(0.70) every concept would become SAME_AS with every other — on the demo corpus that was
~20 000 edges between ~210 concepts. For e5 only the fuzzy step may link concepts.
"""

from typing import Any

import pytest

from app.graph import resolve


class FakeEmbedder:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        # every label gets the same direction → cosine 1.0 for every pair
        return [[1.0, 0.0] for _ in texts]


def _concepts() -> list[dict[str, Any]]:
    return [
        {"id": "c:zaklady", "type": "concept", "label": "Základy", "norm_key": "zaklady"},
        {"id": "c:strecha", "type": "concept", "label": "Strecha", "norm_key": "strecha"},
        {"id": "c:crane", "type": "concept", "label": "Tower crane", "norm_key": "tower crane"},
    ]


@pytest.fixture
def fake_graph(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    written: list[tuple[str, str, str]] = []
    # each concept is evidenced in a different document → all pairs are candidates
    edges = [
        {"type": "MENTIONS", "dst": "c:zaklady", "evidence_chunk_id": "doc1:0"},
        {"type": "MENTIONS", "dst": "c:strecha", "evidence_chunk_id": "doc2:0"},
        {"type": "MENTIONS", "dst": "c:crane", "evidence_chunk_id": "doc3:0"},
    ]
    monkeypatch.setattr(resolve.db, "concept_nodes", _concepts)
    monkeypatch.setattr(resolve.db, "list_graph_edges", lambda: edges)

    def upsert(src: str, dst: str, type_: str, **_: Any) -> None:
        written.append((src, dst, type_))

    monkeypatch.setattr(resolve.db, "upsert_graph_edge", upsert)
    return written


def test_emb_threshold_per_model() -> None:
    assert resolve.emb_threshold("BAAI/bge-m3") == 0.70
    assert resolve.emb_threshold("intfloat/multilingual-e5-small") is None
    assert resolve.emb_threshold("some/other-model") == resolve.DEFAULT_EMB_THRESHOLD


def test_e5_skips_embedding_same_as(
    monkeypatch: pytest.MonkeyPatch, fake_graph: list[tuple[str, str, str]]
) -> None:
    monkeypatch.setattr(
        resolve, "get_embedder", lambda: FakeEmbedder("intfloat/multilingual-e5-small")
    )
    assert resolve.resolve_same_as(["c:crane"]) == 0
    assert fake_graph == []


def test_bge_m3_keeps_embedding_same_as(
    monkeypatch: pytest.MonkeyPatch, fake_graph: list[tuple[str, str, str]]
) -> None:
    monkeypatch.setattr(resolve, "get_embedder", lambda: FakeEmbedder("BAAI/bge-m3"))
    # identical vectors → the new concept is linked to both older ones
    assert resolve.resolve_same_as(["c:crane"]) == 2
    assert all(t == "SAME_AS" for _, _, t in fake_graph)

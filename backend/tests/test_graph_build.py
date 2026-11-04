import pytest

from app import db
from app.config import get_settings
from app.graph.build import build_document_graph
from app.graph.schema import ExtractedConcept
from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks


class FakeEmbedder:
    model_name = "fake"

    def _vec(self, text: str) -> list[float]:
        # 'prietok' and 'flow rate' → same vector — simulates a cross-lingual space
        return [1.0, 0.0] if ("prietok" in text.lower() or "flow" in text.lower()) else [0.0, 1.0]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _seed_doc(doc_id: str, text: str, concept: str, monkeypatch: pytest.MonkeyPatch) -> None:
    db.insert_document(doc_id, f"{doc_id}.pdf", f"sha-{doc_id}")
    db.set_pages(doc_id, [(1, len(text))])
    chunks = [Chunk(f"{doc_id}:t0", doc_id, text, text, 1, 1, "Úvod", "text", None)]
    replace_document_chunks(doc_id, chunks, [[1.0, 0.0]])
    monkeypatch.setattr(
        "app.graph.build.extract_concepts_llm",
        lambda t, c=concept: [ExtractedConcept(name=c, type="concept", defined_here=False)],
    )


@pytest.fixture
def graph_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    get_settings().ensure_dirs()
    db.init_db()
    monkeypatch.setattr("app.graph.resolve.get_embedder", FakeEmbedder)
    return monkeypatch


def test_build_creates_nodes_and_evidence(graph_env: pytest.MonkeyPatch) -> None:
    _seed_doc("d1", "Prietok média bol 12.4 l/min.", "prietok média", graph_env)
    counts = build_document_graph("d1")
    assert counts["chunks"] == 1
    assert counts["quantities"] >= 1
    assert counts["concepts"] == 1

    node_ids = {n["id"] for n in db.list_graph_nodes()}
    assert "doc:d1" in node_ids
    assert "page:d1:1" in node_ids
    assert "chunk:d1:t0" in node_ids
    assert any(n.startswith("quantity:") for n in node_ids)

    mentions = [e for e in db.list_graph_edges() if e["type"] == "MENTIONS"]
    assert mentions and mentions[0]["evidence_chunk_id"] == "d1:t0"


def test_cross_document_same_as(graph_env: pytest.MonkeyPatch) -> None:
    _seed_doc("d1", "Prietok média bol 12.4 l/min.", "prietok chladiaceho média", graph_env)
    build_document_graph("d1")
    _seed_doc("d2", "The coolant flow rate was 12.4 l/min.", "coolant flow rate", graph_env)
    counts = build_document_graph("d2")

    same_as = [e for e in db.list_graph_edges() if e["type"] == "SAME_AS"]
    assert counts["same_as"] >= 1
    assert same_as, "cross-lingual SAME_AS mal vzniknúť cez embedding podobnosť"


def test_same_doc_concepts_never_same_as(graph_env: pytest.MonkeyPatch) -> None:
    """Co-mentioned concepts in one document must not get SAME_AS
    even with high embedding similarity (false pairs like zariadenie↔materiál)."""
    db.insert_document("d1", "d1.pdf", "sha-d1")
    db.set_pages("d1", [(1, 50)])
    replace_document_chunks(
        "d1",
        [Chunk("d1:t0", "d1", "prietok a flow rate spolu", "…", 1, 1, "", "text", None)],
        [[1.0, 0.0]],
    )
    # two concepts with an identical fake vector (both contain 'prietok'/'flow')
    graph_env.setattr(
        "app.graph.build.extract_concepts_llm",
        lambda t: [
            ExtractedConcept(name="prietok média", type="concept"),
            ExtractedConcept(name="flow rate", type="concept"),
        ],
    )
    counts = build_document_graph("d1")
    assert counts["same_as"] == 0
    assert not [e for e in db.list_graph_edges() if e["type"] == "SAME_AS"]


def test_rebuild_is_idempotent(graph_env: pytest.MonkeyPatch) -> None:
    _seed_doc("d1", "Prietok 12.4 l/min.", "prietok", graph_env)
    build_document_graph("d1")
    first_nodes = len(db.list_graph_nodes())
    first_edges = len(db.list_graph_edges())

    build_document_graph("d1")
    assert len(db.list_graph_nodes()) == first_nodes
    assert len(db.list_graph_edges()) == first_edges

"""Cross-doc retrieval cez graf: dopyt trafí doc1, graf pritiahne doc2."""

import pytest

from app import db
from app.config import get_settings
from app.graph.build import build_document_graph
from app.graph.schema import ExtractedConcept
from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks
from app.retrieve.graph_expand import expand_via_graph, is_relational_query
from app.retrieve.pipeline import retrieve_sources
from tests.test_graph_build import FakeEmbedder


@pytest.fixture
def two_docs(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings().ensure_dirs()
    db.init_db()
    monkeypatch.setattr("app.graph.resolve.get_embedder", FakeEmbedder)
    monkeypatch.setattr("app.retrieve.pipeline.get_embedder", FakeEmbedder)

    def seed(doc_id: str, text: str, concept: str, vec: list[float]) -> None:
        db.insert_document(doc_id, f"{doc_id}.pdf", f"sha-{doc_id}")
        db.set_pages(doc_id, [(1, len(text))])
        replace_document_chunks(
            doc_id,
            [Chunk(f"{doc_id}:t0", doc_id, text, text, 1, 1, "", "text", None)],
            [vec],
        )
        monkeypatch.setattr(
            "app.graph.build.extract_concepts_llm",
            lambda t, c=concept: [ExtractedConcept(name=c, type="concept")],
        )
        build_document_graph(doc_id)

    # doc1 SK — vektor blízky dopytu; doc2 EN — vektor ďaleko (dense ho netrafí)
    seed("d1", "Prietok chladiaceho média bol 12.4 l/min.", "prietok chladiaceho média", [1.0, 0.0])
    seed("d2", "The coolant flow rate must be calibrated monthly.", "coolant flow rate", [0.0, 1.0])

    # padding: 30 chunkov bližších k dopytu než d2 → d2 vypadne z dense top-24
    # a bez grafu sa k nemu retrieval nedostane
    db.insert_document("pad", "pad.pdf", "sha-pad")
    pad_chunks = [
        Chunk(f"pad:t{i}", "pad", f"výplňový text {i}", f"výplň {i}", 1, 1, "", "text", None)
        for i in range(30)
    ]
    replace_document_chunks("pad", pad_chunks, [[0.8, 0.2] for _ in range(30)])


def test_expand_via_graph_crosses_documents(two_docs: None) -> None:
    expanded = expand_via_graph(["d1:t0"])
    assert any(r["doc_id"] == "d2" for r in expanded), (
        "SAME_AS klaster mal pritiahnuť chunk z druhého dokumentu"
    )


def test_pipeline_flag_controls_expansion(
    two_docs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_GRAPH_EXPANSION", "false")
    get_settings.cache_clear()
    without = retrieve_sources("prietok chladiaceho média", k=4)
    assert without[0].doc_id == "d1"
    assert "d2" not in {s.doc_id for s in without}, "bez grafu sa d2 nemá ako nájsť"

    monkeypatch.setenv("RAG_GRAPH_EXPANSION", "true")
    get_settings.cache_clear()
    with_graph = retrieve_sources("prietok chladiaceho média", k=4)
    assert "d2" in {s.doc_id for s in with_graph}, "graf expanzia mala pridať cross-doc zdroj"


def test_relational_query_detection() -> None:
    assert is_relational_query("Aký je súvis medzi prietokom a tlakom?")
    assert is_relational_query("What is the relation between flow and pressure?")
    assert not is_relational_query("Aký bol prietok vzorky A-02?")


def test_relational_query_pulls_concept_evidence(two_docs: None) -> None:
    sources = retrieve_sources("Ako súvisí prietok chladiaceho média medzi dokumentmi?", k=6)
    assert {"d1", "d2"} <= {s.doc_id for s in sources}

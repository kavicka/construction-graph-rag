import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.graph import _node_detail
from app.auth import AccessScope, reset_scope, set_scope
from app.config import get_settings
from app.graph.build import build_document_graph
from app.graph.schema import ExtractedConcept
from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks
from app.main import app
from tests.test_graph_build import FakeEmbedder


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    get_settings().ensure_dirs()
    db.init_db()
    monkeypatch.setattr("app.graph.resolve.get_embedder", FakeEmbedder)
    monkeypatch.setattr(
        "app.graph.build.extract_concepts_llm",
        lambda t: [ExtractedConcept(name="prietok média", type="concept", defined_here=True)],
    )
    db.insert_document("d1", "d1.pdf", "sha-d1")
    db.set_pages("d1", [(1, 100)])
    chunks = [Chunk("d1:t0", "d1", "Prietok média 12.4 l/min", "Prietok…", 1, 1, "", "text", None)]
    replace_document_chunks("d1", chunks, [[1.0, 0.0]])
    build_document_graph("d1")
    return TestClient(app)


def test_graph_endpoint(client: TestClient) -> None:
    with client:
        g = client.get("/api/graph").json()
        assert g["total_nodes"] >= 4
        types = {n["type"] for n in g["nodes"]}
        assert {"document", "page", "chunk", "concept"} <= types
        assert g["links"]

        only_concepts = client.get("/api/graph", params={"types": "concept"}).json()
        assert {n["type"] for n in only_concepts["nodes"]} == {"concept"}

        scoped = client.get("/api/graph", params={"doc_id": "d1"}).json()
        assert any(n["id"] == "doc:d1" for n in scoped["nodes"])


def test_node_detail_with_evidence(client: TestClient) -> None:
    with client:
        detail = client.get("/api/graph/node/concept:prietok-media").json()
        assert detail["label"] == "prietok média"
        assert detail["neighbors"]
        assert detail["evidence"]
        ev = detail["evidence"][0]
        assert ev["edge_type"] == "DEFINES"
        assert ev["filename"] == "d1.pdf"
        assert ev["page_from"] == 1

        assert client.get("/api/graph/node/concept:neexistuje").status_code == 404


@pytest.fixture
def shared_concept() -> None:
    """concept:x is evidenced by a public (d1) and a restricted (d2) chunk."""
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "d1.pdf", "sha-d1")
    db.insert_document("d2", "d2.pdf", "sha-d2")
    db.set_document_access("d2", json.dumps(["financie"]))
    db.upsert_graph_node("concept:x", "concept", "pojem x")
    db.upsert_graph_node("concept:y", "concept", "pojem y")
    for doc_id, page in (("d1", 1), ("d2", 2)):
        props = json.dumps({"page_from": page, "snippet": f"úsek {doc_id}"})
        db.upsert_graph_node(f"chunk:{doc_id}:t0", "chunk", "úsek", doc_id=doc_id, props_json=props)
        db.upsert_graph_edge(
            f"chunk:{doc_id}:t0", "concept:x", "MENTIONS", evidence_chunk_id=f"{doc_id}:t0"
        )
    db.upsert_graph_edge("concept:x", "concept:y", "SAME_AS")


def test_node_detail_batches_lookups_and_respects_scope(
    shared_concept: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real_get_node = db.get_graph_node

    def counting_get_node(node_id: str) -> dict[str, Any] | None:
        calls.append(node_id)
        return real_get_node(node_id)

    monkeypatch.setattr(db, "get_graph_node", counting_get_node)

    detail = _node_detail("concept:x")
    assert detail is not None
    assert calls == ["concept:x"]  # neighbours and evidence come from one batched lookup
    assert [n.id for n in detail.neighbors] == ["chunk:d1:t0", "chunk:d2:t0", "concept:y"]
    assert sorted((e.doc_id, e.filename, e.page_from, e.snippet) for e in detail.evidence) == [
        ("d1", "d1.pdf", 1, "úsek d1"),
        ("d2", "d2.pdf", 2, "úsek d2"),
    ]

    member = AccessScope(user_id="u1", email=None, role="member", groups=frozenset())
    token = set_scope(member)
    try:
        detail = _node_detail("concept:x")
    finally:
        reset_scope(token)
    assert detail is not None
    assert [n.id for n in detail.neighbors] == ["chunk:d1:t0", "concept:y"]
    assert [(e.doc_id, e.filename) for e in detail.evidence] == [("d1", "d1.pdf")]

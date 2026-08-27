import pytest
from fastapi.testclient import TestClient

from app import db
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

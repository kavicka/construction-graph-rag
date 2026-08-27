import pytest
from fastapi.testclient import TestClient

from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks
from app.main import app


class FakeEmbedder:
    model_name = "fake"

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0] if "prietok" in text else [0.0, 1.0]


@pytest.fixture
def seeded_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.api.search.get_embedder", FakeEmbedder)
    chunks = [
        Chunk("d1:t0", "d1", "meranie prietoku média", "…", 1, 1, "", "text", None),
        Chunk("d1:t1", "d1", "kalibrácia snímača", "…", 2, 2, "", "text", None),
    ]
    replace_document_chunks("d1", chunks, [[1.0, 0.0], [0.0, 1.0]])
    return TestClient(app)


def test_hybrid_search_endpoint(seeded_client: TestClient) -> None:
    with seeded_client as client:
        hits = client.get("/api/search", params={"q": "prietok média"}).json()
        assert hits, "hybrid musí niečo vrátiť"
        assert hits[0]["chunk_id"] == "d1:t0"
        assert hits[0]["rrf_score"] is not None


def test_dense_only_and_validation(seeded_client: TestClient) -> None:
    with seeded_client as client:
        hits = client.get("/api/search", params={"q": "kalibrácia", "mode": "fts"}).json()
        assert hits[0]["chunk_id"] == "d1:t1"
        assert client.get("/api/search", params={"q": ""}).status_code == 422

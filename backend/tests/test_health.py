from fastapi.testclient import TestClient

from app.main import app


def test_health_ok() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ram_profile"] in ("low", "high")
    assert body["embedding_model"]
    # ollama môže byť down — endpoint musí aj tak odpovedať 200
    assert set(body["ollama"].keys()) == {"up", "models"}


def test_profile_embedding_mapping() -> None:
    from app.config import Settings

    low = Settings(ram_profile="low")
    high = Settings(ram_profile="high")
    assert low.effective_embedding_model == "intfloat/multilingual-e5-small"
    assert high.effective_embedding_model == "BAAI/bge-m3"
    override = Settings(ram_profile="low", embedding_model="BAAI/bge-m3")
    assert override.effective_embedding_model == "BAAI/bge-m3"

import pytest
from fastapi.testclient import TestClient

from app.main import app

FAKE_PDF = b"%PDF-1.4 fake obsah pre testy"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    scheduled: list[str] = []
    monkeypatch.setattr("app.api.documents.schedule_ingest", scheduled.append)
    return TestClient(app)


def _upload(client: TestClient, name: str = "test.pdf", data: bytes = FAKE_PDF) -> dict:  # type: ignore[type-arg]
    resp = client.post("/api/documents", files=[("files", (name, data, "application/pdf"))])
    assert resp.status_code == 201, resp.text
    return resp.json()[0]


def test_upload_and_list(client: TestClient) -> None:
    with client:
        doc = _upload(client)
        assert doc["status"] == "queued"
        docs = client.get("/api/documents").json()
        assert [d["id"] for d in docs] == [doc["id"]]


def test_upload_dedupe_same_content(client: TestClient) -> None:
    with client:
        first = _upload(client)
        second = _upload(client, name="iny-nazov.pdf")
        assert second["id"] == first["id"]
        assert len(client.get("/api/documents").json()) == 1


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    with client:
        resp = client.post("/api/documents", files=[("files", ("x.txt", b"nie pdf", "text/plain"))])
        assert resp.status_code == 415


def test_detail_404_and_pdf_serving(client: TestClient) -> None:
    with client:
        assert client.get("/api/documents/neexistuje").status_code == 404
        doc = _upload(client)
        detail = client.get(f"/api/documents/{doc['id']}").json()
        assert detail["pages"] == []
        assert detail["tables"] == []
        pdf = client.get(f"/api/documents/{doc['id']}/pdf")
        assert pdf.status_code == 200
        assert pdf.content == FAKE_PDF

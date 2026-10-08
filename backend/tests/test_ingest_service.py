import pytest

from app import db
from app.config import get_settings
from app.ingest.service import _run_ingest


@pytest.mark.anyio
async def test_ingest_error_path(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "a.pdf", "sha-1")

    def boom(doc_id: str) -> dict[str, int]:
        raise RuntimeError("parse spadol")

    monkeypatch.setattr("app.ingest.service.parse_document", boom)
    await _run_ingest("d1")

    doc = db.get_document("d1")
    assert doc is not None
    assert doc["status"] == "error"
    assert "parse spadol" in (doc["error"] or "")


@pytest.mark.anyio
async def test_ingest_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "a.pdf", "sha-1")

    monkeypatch.setattr(
        "app.ingest.service.parse_document", lambda doc_id: {"pages": 3, "tables": 1}
    )
    monkeypatch.setattr("app.ingest.service._index_document", lambda doc_id: 5)
    await _run_ingest("d1")

    doc = db.get_document("d1")
    assert doc is not None
    assert (doc["status"], doc["stage_progress"]) == ("done", 1.0)


def test_startup_recovers_stuck_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("stuck", "a.pdf", "sha-s")
    db.set_document_status("stuck", "embedding", progress=0.8)
    db.insert_document("ok", "b.pdf", "sha-ok")
    db.set_document_status("ok", "done", progress=1.0)

    recovered: list[str] = []
    monkeypatch.setattr("app.main.schedule_ingest", recovered.append)
    with TestClient(app):
        pass
    assert recovered == ["stuck"]

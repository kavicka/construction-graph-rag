import io
import os
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db
from app.config import get_settings
from app.index.chunker import Chunk
from app.index.lancedb_store import count_chunks, replace_document_chunks
from app.ingest.formats import detect_format
from app.ingest.inbox import inbox_dir, sync_inbox
from app.main import app


def _office(package_dir: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(f"{package_dir}document.xml", "<x/>")
    return buf.getvalue()


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("a.pdf", b"%PDF-1.7 ...", "pdf"),
        ("scan.bin", b"%PDF-1.4", "pdf"),  # PDF by header
        ("zmluva.docx", _office("word/"), "docx"),
        ("deck.pptx", _office("ppt/"), "pptx"),
        ("tab.xlsx", _office("xl/"), "xlsx"),
        ("fake.docx", _office("ppt/"), None),  # zip, but not Word
        ("fake.docx", b"not a zip", None),
        ("page.htm", "<h1>Ťažký</h1>".encode(), "html"),
        ("notes.md", "# Poznámky".encode(), "md"),
        ("notes.md", b"\xff\xfe\x00binary", None),
        ("x.txt", b"plain", None),
    ],
)
def test_detect_format(name: str, data: bytes, expected: str | None) -> None:
    assert detect_format(name, data) == expected


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    scheduled: list[str] = []
    monkeypatch.setattr("app.api.documents.schedule_ingest", scheduled.append)
    return TestClient(app)


def test_upload_markdown_and_serve_original(client: TestClient) -> None:
    with client:
        md = [("files", ("n.md", b"# Ahoj", "text/markdown"))]
        resp = client.post("/api/documents", files=md)
        assert resp.status_code == 201
        doc = resp.json()[0]
        assert doc["format"] == "md"
        assert client.get(f"/api/documents/{doc['id']}/pdf").status_code == 404
        original = client.get(f"/api/documents/{doc['id']}/file")
        assert original.status_code == 200
        assert original.content == b"# Ahoj"
        assert original.headers["content-type"].startswith("text/markdown")
        bad = client.post("/api/documents", files=[("files", ("x.txt", b"x", "text/plain"))])
        assert bad.status_code == 415
        assert "docx" in bad.json()["detail"]


def test_delete_document_everywhere(client: TestClient) -> None:
    with client:
        doc = client.post(
            "/api/documents", files=[("files", ("a.pdf", b"%PDF-1.4 x", "application/pdf"))]
        ).json()[0]
        doc_id = doc["id"]
        replace_document_chunks(
            doc_id,
            [Chunk(f"{doc_id}:t0", doc_id, "text", "text", 1, 1, "", "text", None)],
            [[1.0, 0.0]],
        )
        db.replace_chunk_boxes(doc_id, {f"{doc_id}:t0": [[1, 0, 0, 1, 1]]})
        chunk = client.get(f"/api/chunks/{doc_id}:t0").json()
        assert chunk["filename"] == "a.pdf"

        assert client.delete(f"/api/documents/{doc_id}").status_code == 409  # still queued
        db.set_document_status(doc_id, "done", progress=1.0)
        assert client.delete(f"/api/documents/{doc_id}").status_code == 204

        assert client.get(f"/api/documents/{doc_id}").status_code == 404
        assert count_chunks(doc_id) == 0
        assert db.get_chunk_boxes([f"{doc_id}:t0"]) == {}
        assert not (get_settings().pdf_dir / f"{doc_id}.pdf").exists()
        assert client.get(f"/api/chunks/{doc_id}:t0").status_code == 404
        assert client.delete(f"/api/documents/{doc_id}").status_code == 404


def _write(path: Path, data: bytes, age: float = 10.0) -> None:
    path.write_bytes(data)
    old = time.time() - age
    os.utime(path, (old, old))


def test_inbox_sync_add_update_remove() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    inbox = inbox_dir()
    _write(inbox / "a.pdf", b"%PDF-1.4 verzia 1")
    _write(inbox / "fresh.pdf", b"%PDF-1.4 copying", age=0)
    _write(inbox / "ignore.txt", b"txt")

    first = sync_inbox()
    assert len(first.added) == 1 and not first.updated and not first.removed
    doc = db.get_document(first.added[0])
    assert doc is not None and doc["source_path"] == "a.pdf"
    assert sync_inbox().added == []  # nothing without a change

    db.set_document_status(first.added[0], "done", progress=1.0)
    db.set_document_access(first.added[0], '["financie"]')
    _write(inbox / "a.pdf", b"%PDF-1.4 verzia 2")
    second = sync_inbox()
    assert len(second.updated) == 1
    assert db.get_document(first.added[0]) is None  # old version gone
    new_doc = db.get_document(second.updated[0])
    assert new_doc is not None and new_doc["access_json"] == '["financie"]'  # access rights kept

    new_id = second.updated[0]
    db.set_document_status(new_id, "done", progress=1.0)
    (inbox / "a.pdf").unlink()
    (inbox / "fresh.pdf").unlink()
    third = sync_inbox()
    assert third.removed == [new_id]
    assert db.list_documents() == []

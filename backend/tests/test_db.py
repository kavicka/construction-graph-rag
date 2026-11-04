from app import db
from app.config import get_settings


def _setup() -> None:
    get_settings().ensure_dirs()
    db.init_db()


def test_document_crud_and_dedupe() -> None:
    _setup()
    db.insert_document("d1", "a.pdf", "sha-1")
    assert db.find_document_by_sha("sha-1")["id"] == "d1"  # type: ignore[index]
    assert db.find_document_by_sha("neexistuje") is None

    doc = db.get_document("d1")
    assert doc is not None
    assert doc["status"] == "queued"

    db.set_document_status("d1", "error", error="bum")
    doc = db.get_document("d1")
    assert doc is not None
    assert (doc["status"], doc["error"]) == ("error", "bum")


def test_pages_and_tables() -> None:
    _setup()
    db.insert_document("d1", "a.pdf", "sha-1")
    db.set_pages("d1", [(1, 120), (2, 0)])
    pages = db.get_pages("d1")
    assert [p["page_no"] for p in pages] == [1, 2]
    doc = db.get_document("d1")
    assert doc is not None
    assert doc["pages_count"] == 2

    db.insert_table("t1", "d1", 2, "Tab. 1", 4, 3, "/tmp/t1.md", None)
    tables = db.list_tables("d1")
    assert len(tables) == 1
    assert tables[0]["caption"] == "Tab. 1"

    # re-ingest must replace the tables, not duplicate them
    db.delete_document_tables("d1")
    assert db.list_tables("d1") == []

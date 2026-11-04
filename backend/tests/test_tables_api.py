"""GET /api/tables/{id}: markdown + parquet preview, row cap and document access control."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from app import db
from app.api.tables import MAX_ROWS
from app.config import get_settings
from app.main import app

ADMIN = ("admin@firma.sk", "admin-heslo-123")
MEMBER = ("alica@firma.sk", "alica-heslo-1")


def _write_parquet(path: Path, n_rows: int) -> None:
    table = pa.table(
        {
            "Položka": [f"Položka {i}" for i in range(n_rows)],
            "Cena": [float(i) for i in range(n_rows)],
        }
    )
    pq.write_table(table, str(path))


def _seed_table(
    tmp_path: Path,
    *,
    table_id: str = "t1",
    doc_id: str = "d1",
    n_rows: int = 3,
    markdown: str | None = "| Položka | Cena |\n|---|---|\n| Betón | 120 |",
    with_parquet: bool = True,
    access: list[str] | None = None,
    caption: str | None = "Cenník",
) -> None:
    if db.get_document(doc_id) is None:
        db.insert_document(doc_id, f"{doc_id}.pdf", f"sha-{doc_id}")
        if access is not None:
            db.set_document_access(doc_id, json.dumps(access))
    md_path = tmp_path / f"{table_id}.md"
    if markdown is not None:
        md_path.write_text(markdown, encoding="utf-8")
    parquet_path: str | None = None
    if with_parquet:
        pq_path = tmp_path / f"{table_id}.parquet"
        _write_parquet(pq_path, n_rows)
        parquet_path = str(pq_path)
    db.insert_table(table_id, doc_id, 4, caption, n_rows, 2, str(md_path), parquet_path)
    db.set_table_summary(table_id, "Cenník betónu.")


def test_returns_metadata_markdown_and_rows(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path)
        resp = client.get("/api/tables/t1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "t1" and body["doc_id"] == "d1"
    assert body["page_no"] == 4 and body["caption"] == "Cenník"
    assert body["summary"] == "Cenník betónu."
    assert body["n_rows"] == 3 and body["n_cols"] == 2
    assert "| Betón | 120 |" in body["markdown"]
    assert body["columns"] == ["Položka", "Cena"]
    assert body["rows"] == [["Položka 0", 0.0], ["Položka 1", 1.0], ["Položka 2", 2.0]]
    assert body["truncated"] is False


def test_unknown_table_is_404() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/tables/neexistuje")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "tabuľka neexistuje"


def test_rows_are_capped_and_flagged_truncated(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path, n_rows=MAX_ROWS + 25)
        body = client.get("/api/tables/t1").json()
    assert len(body["rows"]) == MAX_ROWS
    assert body["truncated"] is True
    assert body["rows"][-1][0] == f"Položka {MAX_ROWS - 1}"  # head of the table, in order
    assert body["n_rows"] == MAX_ROWS + 25  # metadata still reports the real size


def test_exactly_max_rows_is_not_truncated(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path, n_rows=MAX_ROWS)
        body = client.get("/api/tables/t1").json()
    assert len(body["rows"]) == MAX_ROWS
    assert body["truncated"] is False


def test_missing_parquet_file_gives_markdown_only(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path, with_parquet=False)
        body = client.get("/api/tables/t1").json()
    assert body["columns"] == [] and body["rows"] == []
    assert body["truncated"] is False
    assert "Betón" in body["markdown"]


def test_parquet_path_recorded_but_file_deleted(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path)
        (tmp_path / "t1.parquet").unlink()
        body = client.get("/api/tables/t1").json()
    assert body["rows"] == [] and body["columns"] == []
    assert "Betón" in body["markdown"]


def test_missing_markdown_file_gives_empty_markdown(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path, markdown=None)
        body = client.get("/api/tables/t1").json()
    assert body["markdown"] == ""
    assert len(body["rows"]) == 3


def test_table_without_caption_or_summary(tmp_path: Path) -> None:
    with TestClient(app) as client:
        db.insert_document("d1", "d1.pdf", "sha")
        db.insert_table("t9", "d1", None, None, 0, 0, str(tmp_path / "none.md"), None)
        body = client.get("/api/tables/t9").json()
    assert body["caption"] is None and body["summary"] is None and body["page_no"] is None
    assert body["rows"] == [] and body["markdown"] == ""


def test_non_ascii_cells_survive(tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path, markdown="| Názov |\n|---|\n| Žilinský ďateľ |")
        resp = client.get("/api/tables/t1")
    assert "Žilinský ďateľ" in resp.json()["markdown"]
    assert resp.json()["rows"][0][0] == "Položka 0"


# --- access control --------------------------------------------------------------------


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("RAG_ADMIN_EMAIL", ADMIN[0])
    monkeypatch.setenv("RAG_ADMIN_PASSWORD", ADMIN[1])
    get_settings.cache_clear()


def _login(client: TestClient, creds: tuple[str, str]) -> None:
    resp = client.post("/api/auth/login", json={"email": creds[0], "password": creds[1]})
    assert resp.status_code == 200, resp.text


def test_table_of_hidden_document_is_404_for_member(secured: None, tmp_path: Path) -> None:
    with TestClient(app) as admin:
        _login(admin, ADMIN)
        created = admin.post(
            "/api/users",
            json={
                "email": MEMBER[0],
                "name": "Alica",
                "password": MEMBER[1],
                "groups": ["stavba"],
            },
        )
        assert created.status_code == 201
        _seed_table(tmp_path, table_id="t-fin", doc_id="fin", access=["financie"])
        _seed_table(tmp_path, table_id="t-stav", doc_id="stav", access=["stavba"])
        _seed_table(tmp_path, table_id="t-all", doc_id="all", access=["*"])
        assert admin.get("/api/tables/t-fin").status_code == 200  # admin sees everything

    with TestClient(app) as alica:
        _login(alica, MEMBER)
        # existence of a forbidden table must not leak: same 404 as an unknown id
        hidden = alica.get("/api/tables/t-fin")
        assert hidden.status_code == 404
        assert hidden.json() == alica.get("/api/tables/neexistuje").json()
        assert alica.get("/api/tables/t-stav").status_code == 200
        assert alica.get("/api/tables/t-all").status_code == 200


def test_anonymous_request_is_rejected_when_login_is_on(secured: None, tmp_path: Path) -> None:
    with TestClient(app) as client:
        _seed_table(tmp_path)
        assert client.get("/api/tables/t1").status_code == 401

"""Fáza 1: pracovníci, PIN prihlásenie so zámkom, pracovník smie len dochádzku."""

import hashlib

import pytest
from fastapi.testclient import TestClient

from app.auth import resolve_token
from app.workforce import store
from app.workforce.workers import check_pin, create_worker, create_worker_session
from tests.wf_fixtures import login_admin, make_worker


def test_worker_crud_without_auth(client: TestClient) -> None:
    wid = make_worker(client, "Jano Murár", professions=["murár", " murár ", "tesár"])
    workers = client.get("/api/workers").json()
    assert workers == [
        {
            "id": wid,
            "name": "Jano Murár",
            "professions": ["murár", "tesár"],
            "active": True,
            "locked": False,
        }
    ]
    assert client.post("/api/workers", json={"name": "X", "pin": "12"}).status_code == 422
    assert client.post("/api/workers", json={"name": "X", "pin": "12ab"}).status_code == 422

    resp = client.put(
        f"/api/workers/{wid}", json={"name": "Ján Murár", "professions": ["murár"], "active": False}
    )
    assert resp.json()["name"] == "Ján Murár" and resp.json()["active"] is False
    assert client.put(f"/api/workers/{wid}/pin", json={"pin": "9876"}).status_code == 204
    assert client.delete(f"/api/workers/{wid}").status_code == 204
    assert client.get("/api/workers").json() == []
    # bez prihlasovania sa na prihlasovacej obrazovke mená neukazujú
    assert client.get("/api/auth/workers").json() == []


def test_professions_editable(client: TestClient) -> None:
    assert "murár" in client.get("/api/professions").json()
    resp = client.put("/api/professions", json={"names": ["murár", "", "murár", "geodet"]})
    assert resp.json() == ["murár", "geodet"]
    assert client.get("/api/professions").json() == ["murár", "geodet"]


def test_pin_login_and_worker_guard(secured: None, client: TestClient) -> None:
    login_admin(client)
    wid = make_worker(client, "Fero")
    client.post("/api/auth/logout")

    assert client.get("/api/auth/workers").json() == [{"id": wid, "name": "Fero"}]
    bad = client.post("/api/auth/pin", json={"worker_id": wid, "pin": "0000"})
    assert bad.status_code == 401 and "zostávajú 4 pokusy" in bad.json()["detail"]
    ok = client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1234"})
    assert ok.status_code == 200 and ok.json()["name"] == "Fero"

    me = client.get("/api/auth/me").json()
    assert me["user"]["id"] == wid and me["user"]["role"] == "member"
    # dochádzka a vlastné úlohy áno, RAG a správa nie
    assert client.get("/api/me/tasks").status_code == 200
    assert client.get("/api/me/board").json() == {"tasks": [], "today_ids": []}
    assert client.get("/api/attendance/today").status_code == 200
    for path in ("/api/documents", "/api/chats", "/api/workers", "/api/sites", "/api/tasks"):
        assert client.get(path).status_code == 403, path
    assert client.post("/mcp", json={}).status_code == 403


def test_pin_lockout(secured: None, client: TestClient) -> None:
    login_admin(client)
    wid = make_worker(client, "Ondro")
    client.post("/api/auth/logout")
    for _ in range(4):
        assert (
            client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1111"}).status_code == 401
        )
    assert client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1111"}).status_code == 423
    # ani správny PIN počas zámku nepustí
    assert client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1234"}).status_code == 423
    login_admin(client)
    assert client.get("/api/workers").json()[0]["locked"] is True
    # nový PIN od majiteľa zámok zruší
    client.put(f"/api/workers/{wid}/pin", json={"pin": "4321"})
    assert check_pin(wid, "4321").ok


def test_deactivated_worker_is_logged_out(secured: None, client: TestClient) -> None:
    login_admin(client)
    wid = make_worker(client, "Paľo")
    client.post("/api/auth/logout")
    client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1234"})
    assert client.get("/api/attendance/today").status_code == 200

    client.post("/api/auth/logout")
    login_admin(client)
    client.put(f"/api/workers/{wid}", json={"name": "Paľo", "professions": [], "active": False})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1234"}).status_code == 401
    assert client.get("/api/auth/workers").json() == []


def test_worker_session_compatible_with_auth(client: TestClient) -> None:
    worker = create_worker("Test", "5555", [])
    token, max_age = create_worker_session(worker["id"])
    assert max_age == 90 * 86400
    scope = resolve_token(token, None)
    assert scope is not None and scope.user_id == worker["id"]
    stored = store.get_worker(worker["id"])
    assert stored is not None and stored["pin_hash"].startswith("scrypt$")
    assert hashlib.sha256(token.encode()).hexdigest()  # token sa ukladá len ako hash


def test_bootstrap_admin_after_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prihlasovanie zapnuté až po pridaní pracovníkov — admin musí vzniknúť aj tak."""
    from app.auth import ensure_bootstrap_admin
    from app.config import get_settings
    from app.db import init_db, list_users
    from app.workforce.store import init_workforce_db

    get_settings().ensure_dirs()
    init_db()
    init_workforce_db()
    create_worker("Fero", "1234", [])
    monkeypatch.setenv("RAG_ADMIN_EMAIL", "majitel@firma.sk")
    monkeypatch.setenv("RAG_ADMIN_PASSWORD", "majitel-heslo-123")
    get_settings.cache_clear()
    ensure_bootstrap_admin()
    ensure_bootstrap_admin()  # idempotentné
    assert [u["role"] for u in list_users()].count("admin") == 1

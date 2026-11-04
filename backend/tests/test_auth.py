import json

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import AccessScope, hash_password, reset_scope, set_scope, verify_password
from app.config import get_settings
from app.index.chunker import Chunk
from app.index.lancedb_store import replace_document_chunks
from app.main import app
from app.retrieve.pipeline import retrieve_sources
from tests.test_graph_build import FakeEmbedder

ADMIN = ("admin@firma.sk", "admin-heslo-123")


def test_password_hashing() -> None:
    stored = hash_password("tajne-heslo-1")
    assert stored.startswith("scrypt$")
    assert verify_password("tajne-heslo-1", stored)
    assert not verify_password("ine-heslo", stored)
    assert not verify_password("x", "bcrypt$zle")


def test_disabled_auth_is_open() -> None:
    with TestClient(app) as client:
        assert client.get("/api/auth/me").json() == {"auth_enabled": False, "user": None}
        assert client.get("/api/documents").status_code == 200


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("RAG_ADMIN_EMAIL", ADMIN[0])
    monkeypatch.setenv("RAG_ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setattr("app.api.documents.schedule_ingest", lambda doc_id: None)
    monkeypatch.setattr("app.retrieve.pipeline.get_embedder", FakeEmbedder)
    monkeypatch.setattr("app.api.search.get_embedder", FakeEmbedder)
    get_settings.cache_clear()


def _login(client: TestClient, email: str, password: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


def _seed_docs() -> None:
    """A = "stavba", B = "financie" (best match for 'prietok'), C = everyone."""
    for doc_id, access, vec in (
        ("A", ["stavba"], [0.6, 0.4]),
        ("B", ["financie"], [1.0, 0.0]),
        ("C", ["*"], [0.5, 0.5]),
    ):
        db.insert_document(doc_id, f"{doc_id}.pdf", f"sha-{doc_id}")
        db.set_document_status(doc_id, "done", progress=1.0)
        db.set_document_access(doc_id, json.dumps(access))
        replace_document_chunks(
            doc_id,
            [
                Chunk(
                    f"{doc_id}:t0",
                    doc_id,
                    f"prietok {doc_id}",
                    f"prietok {doc_id}",
                    1,
                    1,
                    "",
                    "text",
                    None,
                )
            ],
            [vec],
        )


def test_login_roles_and_document_acl(secured: None) -> None:
    with TestClient(app) as admin:
        assert admin.get("/api/documents").status_code == 401
        assert admin.get("/api/health").status_code == 200
        assert admin.get("/api/auth/me").json()["user"] is None
        bad = admin.post("/api/auth/login", json={"email": ADMIN[0], "password": "zle"})
        assert bad.status_code == 401
        _login(admin, *ADMIN)
        assert admin.get("/api/auth/me").json()["user"]["role"] == "admin"

        _seed_docs()
        created = admin.post(
            "/api/users",
            json={
                "email": "alica@firma.sk",
                "name": "Alica",
                "password": "alica-heslo-1",
                "groups": ["stavba"],
            },
        )
        assert created.status_code == 201
        assert (
            admin.post(
                "/api/users",
                json={"email": "alica@firma.sk", "name": "X", "password": "alica-heslo-1"},
            ).status_code
            == 409
        )
        assert (
            admin.post(
                "/api/users", json={"email": "kratke@firma.sk", "name": "X", "password": "kratke"}
            ).status_code
            == 422
        )
        assert {d["id"] for d in admin.get("/api/documents").json()} == {"A", "B", "C"}

    with TestClient(app) as alica:
        _login(alica, "alica@firma.sk", "alica-heslo-1")
        assert {d["id"] for d in alica.get("/api/documents").json()} == {"A", "C"}
        assert alica.get("/api/documents/B").status_code == 404
        assert alica.get("/api/documents/B/file").status_code == 404
        assert alica.get("/api/chunks/B:t0").status_code == 404
        assert alica.delete("/api/documents/A").status_code == 403
        assert alica.get("/api/traces").status_code == 403
        assert alica.get("/api/users").status_code == 403
        assert alica.put("/api/company", json={}).status_code == 403
        assert alica.put("/api/documents/A/access", json={"groups": ["*"]}).status_code == 403
        hits = alica.get("/api/search", params={"q": "prietok"}).json()
        assert {h["doc_id"] for h in hits} == {"A", "C"}

        token = alica.post("/api/auth/tokens", json={"name": "mcp"}).json()["token"]
        alica.post("/api/auth/logout")
        assert alica.get("/api/documents").status_code == 401

    with TestClient(app) as script:
        bearer = {"Authorization": f"Bearer {token}"}
        assert {d["id"] for d in script.get("/api/documents", headers=bearer).json()} == {"A", "C"}
        assert (
            script.get("/api/documents", headers={"Authorization": "Bearer zly"}).status_code == 401
        )


def test_retrieval_prefilters_by_access(secured: None) -> None:
    with TestClient(app):  # lifespan → init_db + bootstrap admin
        _seed_docs()
    member = AccessScope(user_id="u1", email="m@x.sk", role="member", groups=frozenset({"stavba"}))
    token = set_scope(member)
    try:
        sources = retrieve_sources("prietok", k=3)
    finally:
        reset_scope(token)
    # B is the nearest vector, but the filter is applied in the search → B never shows up
    assert {s.doc_id for s in sources} == {"A", "C"}
    # fail closed: with login on, code running outside a request scope sees nothing
    assert retrieve_sources("prietok", k=3) == []
    token = set_scope(AccessScope(user_id="a1", email=None, role="admin"))
    try:
        assert {s.doc_id for s in retrieve_sources("prietok", k=3)} == {"A", "B", "C"}
    finally:
        reset_scope(token)


def test_chats_are_private(secured: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_chat_api import _fake_sources, _fake_stream, _parse_sse

    monkeypatch.setattr("app.retrieve.orchestrator.retrieve_sources", _fake_sources)
    monkeypatch.setattr("app.retrieve.orchestrator.chat_stream", _fake_stream)
    with TestClient(app) as admin:
        _login(admin, *ADMIN)
        for email in ("jan@firma.sk", "eva@firma.sk"):
            admin.post(
                "/api/users", json={"email": email, "name": email, "password": "dlhe-heslo-123"}
            )
    with TestClient(app) as jan:
        _login(jan, "jan@firma.sk", "dlhe-heslo-123")
        events = _parse_sse(jan.post("/api/chat", json={"message": "Aký bol prietok?"}).text)
        chat_id = next(e for e in events if e["type"] == "sources")["chat_id"]
        message_id = next(e for e in events if e["type"] == "done")["message_id"]
        assert [c["id"] for c in jan.get("/api/chats").json()] == [chat_id]
    with TestClient(app) as eva:
        _login(eva, "eva@firma.sk", "dlhe-heslo-123")
        assert eva.get("/api/chats").json() == []
        assert eva.get(f"/api/chats/{chat_id}").status_code == 404
        assert eva.post("/api/chat", json={"message": "x", "chat_id": chat_id}).status_code == 404
        assert (
            eva.put(f"/api/messages/{message_id}/feedback", json={"rating": -1}).status_code == 404
        )


def test_admin_can_reset_password_and_revokes_sessions(secured: None) -> None:
    with TestClient(app) as admin:
        _login(admin, *ADMIN)
        uid = admin.post(
            "/api/users",
            json={
                "email": "eva@firma.sk",
                "name": "Eva",
                "password": "povodne-heslo-1",
                "groups": ["stavba"],
            },
        ).json()["id"]
    with TestClient(app) as eva:
        _login(eva, "eva@firma.sk", "povodne-heslo-1")
        assert eva.get("/api/documents").status_code == 200
        with TestClient(app) as admin2:
            _login(admin2, *ADMIN)
            short = admin2.put(
                f"/api/users/{uid}",
                json={"name": "Eva", "role": "member", "groups": ["stavba"], "password": "kratke"},
            )
            assert short.status_code == 422
            ok = admin2.put(
                f"/api/users/{uid}",
                json={
                    "name": "Eva N.",
                    "role": "member",
                    "groups": ["stavba", "financie"],
                    "password": "nove-heslo-12345",
                },
            )
            assert ok.status_code == 200
            assert ok.json()["groups"] == ["financie", "stavba"] and ok.json()["name"] == "Eva N."
        assert eva.get("/api/documents").status_code == 401  # old session revoked
        old = eva.post(
            "/api/auth/login", json={"email": "eva@firma.sk", "password": "povodne-heslo-1"}
        )
        assert old.status_code == 401
        _login(eva, "eva@firma.sk", "nove-heslo-12345")
    with TestClient(app) as admin3:
        _login(admin3, *ADMIN)
        # without a password in the body the password is unchanged
        keep = admin3.put(
            f"/api/users/{uid}", json={"name": "Eva N.", "role": "member", "groups": []}
        )
        assert keep.status_code == 200
    with TestClient(app) as eva2:
        _login(eva2, "eva@firma.sk", "nove-heslo-12345")

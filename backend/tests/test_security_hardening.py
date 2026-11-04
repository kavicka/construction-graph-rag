"""Security hardening before the public release: audio upload XSS, credential revocation and
API token expiry, PIN brute force, CSRF, fail-closed access scope, LanceDB filter escaping,
bounded uploads, ingest race, no server paths over MCP, localhost-only compose port."""

import hashlib
import io
import json
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from mcp import Client
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.types import Message, Scope

from app import db
from app.auth import (
    LOCAL_ADMIN,
    NO_ACCESS,
    AccessScope,
    create_api_token,
    create_session,
    create_user,
    current_scope,
    reset_scope,
    resolve_token,
    set_scope,
)
from app.config import get_settings
from app.index.chunker import Chunk
from app.index.lancedb_store import (
    count_chunks,
    delete_document_chunks,
    document_chunks,
    replace_document_chunks,
)
from app.ingest.store import store_document
from app.main import app
from app.mcp_server import mcp
from app.retrieve.orchestrator import AnswerResult
from app.security import LoginRateLimiter
from app.workforce import store
from app.workforce.config import get_workforce_settings
from app.workforce.runtime import WorkerGuard
from app.workforce.workers import check_pin, create_worker, create_worker_session

ADMIN = ("admin@firma.sk", "admin-heslo-123")
MEMBER_PASSWORD = "clen-heslo-1234"


@pytest.fixture
def auth_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("RAG_ADMIN_EMAIL", ADMIN[0])
    monkeypatch.setenv("RAG_ADMIN_PASSWORD", ADMIN[1])
    monkeypatch.setattr("app.api.documents.schedule_ingest", lambda doc_id: None)
    get_settings.cache_clear()


@pytest.fixture
def no_ingest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.documents.schedule_ingest", lambda doc_id: None)


@pytest.fixture
def ready_db() -> Iterator[None]:
    get_settings().ensure_dirs()
    db.init_db()
    store.init_workforce_db()
    yield


def _login(client: TestClient, email: str, password: str) -> None:
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- 1 + 7: audio upload / download ------------------------------------------------------


def _upload(name: str, ctype: str | None) -> UploadFile:
    headers = Headers({"content-type": ctype}) if ctype else Headers({})
    return UploadFile(io.BytesIO(b"x"), filename=name, headers=headers)


def test_audio_extension_comes_only_from_allowlist() -> None:
    from app.api.attendance import _audio_ext

    assert _audio_ext(_upload("evil.html", "audio/webm;codecs=opus")) == "webm"
    assert _audio_ext(_upload("nahravka.OGG", "application/octet-stream")) == "ogg"
    assert _audio_ext(_upload("hlas.m4a", None)) == "m4a"
    for name, ctype in (("x.html", "text/html"), ("x.svg", None), ("bez-pripony", None)):
        with pytest.raises(HTTPException) as exc:
            _audio_ext(_upload(name, ctype))
        assert exc.value.status_code == 415


def _report(worker_name: str = "Jano") -> tuple[str, str]:
    worker = create_worker(worker_name, "1234", [])
    report = store.get_or_create_report(worker["id"], date.today().isoformat())
    return worker["id"], report["id"]


def test_audio_upload_rejects_html_and_caps_size(monkeypatch: pytest.MonkeyPatch) -> None:
    with TestClient(app) as client:
        _, rid = _report()
        html = b"<script>fetch('/api/users')</script>"
        resp = client.post(
            f"/api/attendance/reports/{rid}/audio", files={"file": ("x.html", html, "text/html")}
        )
        assert resp.status_code == 415 and "nahrávky" in resp.json()["detail"]

        monkeypatch.setattr("app.api.attendance.MAX_AUDIO_BYTES", 8)
        monkeypatch.setattr("app.api.attendance.UPLOAD_CHUNK_BYTES", 4)
        big = client.post(
            f"/api/attendance/reports/{rid}/audio",
            files={"file": ("x.webm", b"\x1aE\xdf\xa3" * 10, "audio/webm")},
        )
        assert big.status_code == 413
        # nothing half-written stays on disk
        folder = get_workforce_settings().audio_dir / rid
        assert not folder.exists() or list(folder.iterdir()) == []


def test_audio_download_is_sandboxed() -> None:
    with TestClient(app) as client:
        _, rid = _report()
        folder = get_workforce_settings().audio_dir / rid
        folder.mkdir(parents=True, exist_ok=True)
        good = folder / "a.webm"
        good.write_bytes(b"\x1aE\xdf\xa3")
        legacy = folder / "b.html"  # stored before the allowlist existed
        legacy.write_bytes(b"<script>alert(1)</script>")
        good_id = store.insert_input(rid, "audio", "", audio_path=str(good))
        legacy_id = store.insert_input(rid, "audio", "", audio_path=str(legacy))

        resp = client.get(f"/api/attendance/inputs/{good_id}/audio")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/webm"
        assert resp.headers["content-disposition"].startswith("inline")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["content-security-policy"] == "sandbox; default-src 'none'"

        resp = client.get(f"/api/attendance/inputs/{legacy_id}/audio")
        assert resp.headers["content-type"] == "application/octet-stream"
        assert resp.headers["content-disposition"].startswith("attachment")
        assert resp.headers["x-content-type-options"] == "nosniff"


def test_document_upload_size_is_capped_while_reading(
    no_ingest: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.api.documents.MAX_FILE_BYTES", 10)
    monkeypatch.setattr("app.api.uploads.UPLOAD_CHUNK_BYTES", 4)
    with TestClient(app) as client:
        resp = client.post(
            "/api/documents",
            files=[("files", ("a.pdf", b"%PDF-1.4 " + b"x" * 50, "application/pdf"))],
        )
        assert resp.status_code == 413 and "100 MB" in resp.json()["detail"]
        assert list(get_settings().pdf_dir.iterdir()) == []


# --- 2: credentials revocation, token expiry, token endpoints -----------------------------


def test_pin_reset_and_deactivation_revoke_sessions_and_tokens() -> None:
    with TestClient(app) as client:
        worker = create_worker("Fero", "1234", [])
        wid = worker["id"]
        session, _ = create_worker_session(wid)
        token, _ = create_api_token(wid, "legacy")
        assert resolve_token(session, None) and resolve_token(None, token)

        assert client.put(f"/api/workers/{wid}/pin", json={"pin": "9876"}).status_code == 204
        assert resolve_token(session, None) is None
        assert resolve_token(None, token) is None

        session, _ = create_worker_session(wid)
        token, _ = create_api_token(wid, "legacy")
        client.put(f"/api/workers/{wid}", json={"name": "Fero", "professions": [], "active": False})
        assert resolve_token(session, None) is None
        assert resolve_token(None, token) is None


def test_user_password_reset_and_deletion_revoke_credentials(ready_db: None) -> None:
    with TestClient(app) as client:
        user = create_user("jan@firma.sk", "Ján", MEMBER_PASSWORD)
        session = create_session(user["id"])
        token, _ = create_api_token(user["id"], "mcp")
        resp = client.put(
            f"/api/users/{user['id']}",
            json={"name": "Ján", "role": "member", "groups": [], "password": "nove-heslo-1234"},
        )
        assert resp.status_code == 200
        assert resolve_token(session, None) is None and resolve_token(None, token) is None

        token, _ = create_api_token(user["id"], "mcp")
        assert client.delete(f"/api/users/{user['id']}").status_code == 204
        assert resolve_token(None, token) is None
        with db.get_conn() as conn:
            assert conn.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0


def test_api_token_expires(ready_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    user = create_user("eva@firma.sk", "Eva", MEMBER_PASSWORD)
    token, row = create_api_token(user["id"], "mcp")
    assert resolve_token(None, token) is not None
    assert row["expires_at"][:4] >= str(date.today().year)  # ~90 days ahead by default
    assert "token_hash" not in row

    monkeypatch.setenv("RAG_API_TOKEN_TTL_DAYS", "0")
    expired, _ = create_api_token(user["id"], "short")
    assert resolve_token(None, expired) is None


def test_legacy_tokens_get_id_and_expiry_on_migration(ready_db: None) -> None:
    user = create_user("old@firma.sk", "Old", MEMBER_PASSWORD)
    with db.get_conn() as conn:
        for tok, created in (("rag_old", "2000-01-01 00:00:00"), ("rag_new", None)):
            conn.execute(
                """INSERT INTO api_tokens (token_hash, user_id, name, created_at)
                   VALUES (?, ?, ?, COALESCE(?, datetime('now')))""",
                (_token_hash(tok), user["id"], tok, created),
            )
    db.init_db()
    tokens = db.list_api_tokens(user["id"])
    assert all(t["id"] and t["expires_at"] for t in tokens)
    assert resolve_token(None, "rag_old") is None  # created 2000 + 90 days → expired
    assert resolve_token(None, "rag_new") is not None


def test_token_list_and_revoke_endpoints(auth_on: None) -> None:
    with TestClient(app) as admin:
        _login(admin, *ADMIN)
        for email in ("jan@firma.sk", "eva@firma.sk"):
            body = {"email": email, "name": email, "password": MEMBER_PASSWORD}
            assert admin.post("/api/users", json=body).status_code == 201
        admin_token = admin.post("/api/auth/tokens", json={"name": "admin"}).json()

    with TestClient(app) as jan:
        _login(jan, "jan@firma.sk", MEMBER_PASSWORD)
        created = jan.post("/api/auth/tokens", json={"name": "mcp"}).json()
        assert created["token"].startswith("rag_") and created["expires_at"]
        listed = jan.get("/api/auth/tokens").json()
        assert [t["id"] for t in listed] == [created["id"]]
        assert "token" not in listed[0] and "token_hash" not in listed[0]
        assert jan.get("/api/auth/tokens", params={"all_users": True}).status_code == 403
        # someone else's token looks like a missing one
        assert jan.delete(f"/api/auth/tokens/{admin_token['id']}").status_code == 404

    with TestClient(app) as eva:
        _login(eva, "eva@firma.sk", MEMBER_PASSWORD)
        assert eva.delete(f"/api/auth/tokens/{created['id']}").status_code == 404

    with TestClient(app) as admin:
        _login(admin, *ADMIN)
        everyone = admin.get("/api/auth/tokens", params={"all_users": True}).json()
        assert {t["user_email"] for t in everyone} == {ADMIN[0], "jan@firma.sk"}
        assert admin.delete(f"/api/auth/tokens/{created['id']}").status_code == 204
    bearer = {"Authorization": f"Bearer {created['token']}"}
    with TestClient(app) as script:
        assert script.get("/api/documents", headers=bearer).status_code == 401


def test_worker_cannot_mint_or_list_api_tokens(auth_on: None) -> None:
    with TestClient(app) as client:
        _login(client, *ADMIN)
        wid = client.post("/api/workers", json={"name": "Jano", "pin": "1234"}).json()["id"]
        client.post("/api/auth/logout")
        assert (
            client.post("/api/auth/pin", json={"worker_id": wid, "pin": "1234"}).status_code == 200
        )
        assert client.get("/api/auth/me").json()["user"]["id"] == wid
        assert client.post("/api/auth/tokens", json={"name": "x"}).status_code == 403
        assert client.get("/api/auth/tokens").status_code == 403
        assert client.get("/api/users").status_code == 403
        assert client.post("/api/auth/logout").status_code == 204


# --- 3: PIN brute force -------------------------------------------------------------------


def test_parallel_pin_guesses_cannot_exceed_max_attempts(
    ready_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker = create_worker("Ondro", "1234", [])
    verified = 0
    lock = threading.Lock()

    def slow_verify(pin: str, stored: str) -> bool:
        nonlocal verified
        with lock:
            verified += 1
        time.sleep(0.05)  # widen the window between "read counter" and "write counter"
        return False

    monkeypatch.setattr("app.workforce.workers.verify_password", slow_verify)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: check_pin(worker["id"], "0000"), range(40)))
    max_attempts = get_workforce_settings().pin_max_attempts
    assert verified == max_attempts
    assert sum(r.locked for r in results) == 40 - max_attempts + 1
    assert not any(r.ok for r in results)


def test_expired_pin_lock_starts_fresh_round(ready_db: None) -> None:
    worker = create_worker("Paľo", "1234", [])
    for _ in range(5):
        check_pin(worker["id"], "0000")
    assert check_pin(worker["id"], "1234").locked
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE wf_workers SET locked_until = '2000-01-01T00:00:00+00:00' WHERE user_id = ?",
            (worker["id"],),
        )
    first = check_pin(worker["id"], "0000")
    assert not first.locked and first.attempts_left == 4
    assert check_pin(worker["id"], "1234").ok


def test_failed_logins_are_rate_limited_per_ip(
    auth_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAG_LOGIN_MAX_FAILURES", "3")
    with TestClient(app) as client:
        for _ in range(3):
            bad = client.post("/api/auth/login", json={"email": ADMIN[0], "password": "zle-heslo"})
            assert bad.status_code == 401
        blocked = client.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        assert blocked.status_code == 429
        assert "neúspešných" in blocked.json()["detail"] and blocked.headers["retry-after"]
        # the same budget covers worker PIN login
        pin = client.post("/api/auth/pin", json={"worker_id": "x", "pin": "1234"})
        assert pin.status_code == 429


def test_rate_limiter_window_and_forwarded_for() -> None:
    now = [0.0]
    limiter = LoginRateLimiter(2, 60.0, clock=lambda: now[0])
    limiter.record_failure("1.2.3.4")
    limiter.record_failure("1.2.3.4")
    assert limiter.blocked("1.2.3.4") and not limiter.blocked("5.6.7.8")
    now[0] = 61.0
    assert not limiter.blocked("1.2.3.4")

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"6.6.6.6, 9.9.9.9")],
        "client": ("10.0.0.2", 1234),
    }
    request = Request(scope)
    assert limiter.client_key(request) == "10.0.0.2"  # proxy header ignored by default
    trusted = LoginRateLimiter(2, 60.0, trusted_proxy=True)
    assert trusted.client_key(request) == "9.9.9.9"  # right-most = seen by our proxy


# --- 4: CSRF / cross-origin writes ---------------------------------------------------------


def test_cross_origin_writes_are_rejected(no_ingest: None) -> None:
    pdf = [("files", ("a.pdf", b"%PDF-1.4 a", "application/pdf"))]
    with TestClient(app) as client:
        evil = client.post("/api/documents", files=pdf, headers={"Origin": "http://evil.example"})
        assert evil.status_code == 403 and "cudzej" in evil.json()["detail"]
        assert (
            client.post("/api/documents", files=pdf, headers={"Origin": "null"}).status_code == 403
        )
        cross = client.post("/api/documents", files=pdf, headers={"Sec-Fetch-Site": "cross-site"})
        assert cross.status_code == 403
        # look-alike of an allowed origin with a non-numeric "port"
        sneaky = {"Origin": "http://localhost:80.evil.example"}
        assert client.post("/api/documents", files=pdf, headers=sneaky).status_code == 403
        # reads are not blocked, the app's own origin and non-browser clients still work
        assert (
            client.get("/api/documents", headers={"Origin": "http://evil.example"}).status_code
            == 200
        )
        local = client.post(
            "/api/documents", files=pdf, headers={"Origin": "http://localhost:5173"}
        )
        assert local.status_code == 201
        pdf2 = [("files", ("b.pdf", b"%PDF-1.4 b", "application/pdf"))]
        assert client.post("/api/documents", files=pdf2).status_code == 201


# --- 5: fail-closed scope, root_path, MCP end to end --------------------------------------


def test_default_scope_fails_closed_when_auth_enabled(auth_on: None) -> None:
    assert current_scope() is NO_ACCESS
    assert not NO_ACCESS.can_see(["*"])


def test_default_scope_is_local_admin_without_auth() -> None:
    assert current_scope() is LOCAL_ADMIN


@pytest.mark.anyio
async def test_worker_guard_strips_root_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.workforce.runtime.is_worker", lambda user_id: True)
    reached: list[str] = []
    sent: list[dict[str, Any]] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["path"])

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        sent.append(dict(message))

    guard = WorkerGuard(inner)
    token = set_scope(AccessScope(user_id="w1", email=None, role="member"))
    try:
        for path in ("/rag/api/documents", "/rag/mcp", "/rag/api/attendance/today"):
            scope: Scope = {
                "type": "http",
                "method": "GET",
                "path": path,
                "root_path": "/rag",
                "headers": [],
            }
            await guard(scope, receive, send)
    finally:
        reset_scope(token)
    statuses = [m["status"] for m in sent if m["type"] == "http.response.start"]
    assert statuses == [403, 403]
    assert reached == ["/rag/api/attendance/today"]


def _mcp_call(client: TestClient, bearer: str, tool: str, arguments: dict[str, Any]) -> Any:
    resp = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    )
    assert resp.status_code == 200, resp.text
    if resp.headers["content-type"].startswith("text/event-stream"):
        lines = [ln[5:].strip() for ln in resp.text.splitlines() if ln.startswith("data:")]
        payload = json.loads(lines[-1])
    else:
        payload = resp.json()
    return payload["result"]


def _tool_data(result: Any) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        return structured.get("result", structured)
    return json.loads(result["content"][0]["text"])


def test_mcp_http_member_sees_only_permitted_documents(auth_on: None) -> None:
    with TestClient(app, base_url="http://localhost:8000") as admin:
        for doc_id, access in (("A", ["stavba"]), ("B", ["financie"]), ("C", ["*"])):
            db.insert_document(doc_id, f"{doc_id}.pdf", f"sha-{doc_id}")
            db.set_document_status(doc_id, "done", progress=1.0)
            db.set_document_access(doc_id, json.dumps(access))
        _login(admin, *ADMIN)
        body = {
            "email": "clen@firma.sk",
            "name": "Člen",
            "password": MEMBER_PASSWORD,
            "groups": ["stavba"],
        }
        assert admin.post("/api/users", json=body).status_code == 201
        admin_bearer = admin.post("/api/auth/tokens", json={"name": "a"}).json()["token"]

    with TestClient(app, base_url="http://localhost:8000") as member:
        _login(member, "clen@firma.sk", MEMBER_PASSWORD)
        bearer = member.post("/api/auth/tokens", json={"name": "mcp"}).json()["token"]

    with TestClient(app, base_url="http://localhost:8000") as mcp_client:
        docs = _tool_data(_mcp_call(mcp_client, bearer, "list_documents", {}))
        assert {d["filename"] for d in docs} == {"A.pdf", "C.pdf"}
        forbidden = _mcp_call(mcp_client, bearer, "get_document", {"document": "B"})
        assert forbidden.get("isError") is True
        everything = _tool_data(_mcp_call(mcp_client, admin_bearer, "list_documents", {}))
        assert {d["filename"] for d in everything} == {"A.pdf", "B.pdf", "C.pdf"}
        unauthenticated = mcp_client.post("/mcp", json={})
        assert unauthenticated.status_code == 401


# --- 6, 8, 9, 10 ------------------------------------------------------------------------


def _chunk(cid: str, doc: str) -> Chunk:
    return Chunk(cid, doc, "text", "text", 1, 1, "", "text", None)


def test_lancedb_doc_id_filters_are_escaped() -> None:
    replace_document_chunks("d1", [_chunk("d1:t0", "d1")], [[1.0, 0.0]])
    replace_document_chunks("d2", [_chunk("d2:t0", "d2")], [[0.0, 1.0]])
    injected = "d1' OR doc_id = 'd2"
    assert count_chunks(injected) == 0
    assert document_chunks(injected) == []
    delete_document_chunks("x' OR '1'='1")
    assert count_chunks() == 2


def test_concurrent_identical_upload_returns_existing_document(
    ready_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, created = store_document("a.pdf", b"%PDF-1.4 same", "pdf")
    assert created
    real = db.find_document_by_sha
    calls = 0

    def racy(sha: str) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else real(sha)  # the other upload committed in between

    monkeypatch.setattr(db, "find_document_by_sha", racy)
    row, created = store_document("b.pdf", b"%PDF-1.4 same", "pdf")
    assert not created and row["id"] == first["id"]
    assert [p.name for p in get_settings().pdf_dir.iterdir()] == [f"{first['id']}.pdf"]


@pytest.mark.anyio
async def test_mcp_generate_document_returns_no_server_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_answer(question: str) -> AnswerResult:
        document = {"id": "g1", "title": "Report", "url": "/api/generated/g1/pdf"}
        return AnswerResult(mode="generate", answer="hotovo", sources=[], document=document)

    monkeypatch.setattr("app.mcp_server.answer_question", fake_answer)
    async with Client(mcp) as client:
        result = await client.call_tool("generate_document", {"request": "Priprav report"})
    assert result.structured_content is not None
    out = result.structured_content.get("result", result.structured_content)
    assert "path" not in out and out["url"] == "/api/generated/g1/pdf"
    assert str(get_settings().data_dir) not in json.dumps(out)


def test_compose_publishes_frontend_on_localhost_only() -> None:
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    assert '"127.0.0.1:8080:80"' in compose
    assert '- "8080:80"' not in compose

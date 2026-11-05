"""Shared fixtures for the Firma module tests (imported into test_workforce_*)."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.config import get_settings
from app.workforce import store
from app.workforce.config import get_workforce_settings
from app.workforce.runtime import invalidate_worker_cache

ADMIN = ("majitel@firma.sk", "majitel-heslo-123")


@pytest.fixture(autouse=True)
def wf_isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("RAG_DAILY_SUMMARY", "false")
    # a local backend/.env may have login enabled — tests enable it explicitly
    monkeypatch.setenv("RAG_AUTH_ENABLED", "false")
    monkeypatch.delenv("RAG_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("RAG_ADMIN_PASSWORD", "")
    get_workforce_settings.cache_clear()
    invalidate_worker_cache()
    yield
    get_workforce_settings.cache_clear()
    invalidate_worker_cache()


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_AUTH_ENABLED", "true")
    monkeypatch.setenv("RAG_ADMIN_EMAIL", ADMIN[0])
    monkeypatch.setenv("RAG_ADMIN_PASSWORD", ADMIN[1])
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


def login_admin(client: TestClient) -> None:
    resp = client.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
    assert resp.status_code == 200, resp.text


def make_worker(
    client: TestClient, name: str, pin: str = "1234", professions: list[str] | None = None
) -> str:
    resp = client.post(
        "/api/workers", json={"name": name, "pin": pin, "professions": professions or []}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def make_site(client: TestClient, name: str = "Rovinka") -> str:
    resp = client.post("/api/sites", json={"name": name, "address": ""})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def make_task(
    client: TestClient, site_id: str, title: str, assignees: list[str], **extra: Any
) -> dict[str, Any]:
    body = {"site_id": site_id, "title": title, "assignees": assignees, **extra}
    resp = client.post("/api/tasks", json=body)
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


LlmHandler = Callable[[str, str, type[BaseModel]], Any]


class FakeLLM:
    """Replaces _llm_json: returns a canned answer per schema and records the prompt."""

    def __init__(self) -> None:
        self.responses: dict[str, list[Any]] = {}
        self.prompts: list[tuple[str, str]] = []

    def push(self, schema_name: str, payload: dict[str, Any] | None) -> None:
        self.responses.setdefault(schema_name, []).append(payload)

    def __call__(self, prompt: str, system: str, schema: type[BaseModel], **_: Any) -> Any:
        self.prompts.append((schema.__name__, prompt))
        queue = self.responses.get(schema.__name__) or []
        if not queue:
            return None
        payload = queue.pop(0)
        return None if payload is None else schema.model_validate(payload)


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr("app.workforce.extract._llm_json", fake)
    monkeypatch.setattr("app.workforce.importer._llm_json", fake)
    return fake


def worker_ids() -> set[str]:
    return store.worker_ids()

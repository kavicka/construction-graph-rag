import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.retrieve.pipeline import Source


def _fake_sources(query: str, k: int = 8) -> list[Source]:
    return [
        Source(
            n=1,
            chunk_id="d1:t0",
            doc_id="d1",
            filename="vzorka.pdf",
            page_from=2,
            page_to=2,
            type="text",
            table_id=None,
            text="Prietok bol 12.4 l/min.",
            snippet="Prietok bol 12.4 l/min.",
        )
    ]


async def _fake_stream(
    model: str, messages: list[dict[str, str]], **kw: object
) -> AsyncIterator[str]:
    for tok in ["Prietok ", "je 12.4 l/min ", "[1]"]:
        yield tok


def _parse_sse(body: str) -> list[dict]:  # type: ignore[type-arg]
    events = []
    for frame in body.split("\n\n"):
        for line in frame.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr("app.api.chat.retrieve_sources", _fake_sources)
    monkeypatch.setattr("app.api.chat.chat_stream", _fake_stream)
    return TestClient(app)


def test_chat_stream_and_persistence(client: TestClient) -> None:
    with client:
        resp = client.post("/api/chat", json={"message": "Aký bol prietok?"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        types = [e["type"] for e in events]
        assert types[0] == "sources"
        assert types[-1] == "done"
        assert "delta" in types

        chat_id = events[0]["chat_id"]
        answer = "".join(e["text"] for e in events if e["type"] == "delta")
        assert answer == "Prietok je 12.4 l/min [1]"

        msgs = client.get(f"/api/chats/{chat_id}").json()
        assert [m["role"] for m in msgs] == ["user", "assistant"]
        assert msgs[1]["content"] == answer
        assert msgs[1]["sources"][0]["filename"] == "vzorka.pdf"

        chats = client.get("/api/chats").json()
        assert chats[0]["id"] == chat_id
        assert chats[0]["title"].startswith("Aký bol prietok?")


def test_chat_continues_existing(client: TestClient) -> None:
    with client:
        first = _parse_sse(client.post("/api/chat", json={"message": "Prvá?"}).text)
        chat_id = first[0]["chat_id"]
        client.post("/api/chat", json={"message": "Druhá?", "chat_id": chat_id})
        msgs = client.get(f"/api/chats/{chat_id}").json()
        assert len(msgs) == 4
        assert len(client.get("/api/chats").json()) == 1


def test_chat_404_and_validation(client: TestClient) -> None:
    with client:
        assert client.get("/api/chats/neexistuje").status_code == 404
        assert client.post("/api/chat", json={"message": ""}).status_code == 422

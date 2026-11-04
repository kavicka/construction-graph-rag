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
    monkeypatch.setattr("app.retrieve.orchestrator.retrieve_sources", _fake_sources)
    monkeypatch.setattr("app.retrieve.orchestrator.chat_stream", _fake_stream)
    monkeypatch.setattr(
        "app.retrieve.memory.generate_sync",
        lambda *a, **kw: '{"refers_to": "A-01", "question": "Aký bol prietok vo vzorke A-01?"}',
    )
    return TestClient(app)


def test_chat_stream_and_persistence(client: TestClient) -> None:
    with client:
        resp = client.post("/api/chat", json={"message": "Aký bol prietok?"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)

        types = [e["type"] for e in events]
        assert types[0] == "plan"
        assert types[-1] == "done"
        assert "sources" in types
        assert "delta" in types
        assert "verify" in types

        chat_id = next(e for e in events if e["type"] == "sources")["chat_id"]
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
        chat_id = next(e for e in first if e["type"] == "sources")["chat_id"]
        client.post("/api/chat", json={"message": "Druhá?", "chat_id": chat_id})
        msgs = client.get(f"/api/chats/{chat_id}").json()
        assert len(msgs) == 4
        assert len(client.get("/api/chats").json()) == 1


def test_chat_generates_branded_document(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_generate(model: str, prompt: str, **kw: object) -> str:
        if kw.get("json_schema"):
            calls.append("outline")
            return '{"title": "Report o prietoku", "subtitle": "Meranie 2026", "sections": []}'
        calls.append("section")
        return "Prietok bol 12.4 l/min [1]."

    monkeypatch.setattr("app.docgen.generate.generate_sync", fake_generate)
    with client:
        client.put(
            "/api/company",
            json={"name": "Meranie s.r.o."},
        )
        resp = client.post("/api/chat", json={"message": "Priprav report o prietoku"})
        events = _parse_sse(resp.text)
        types = [e["type"] for e in events]
        assert events[0] == {"type": "plan", "mode": "generate"}
        assert types[-1] == "done"
        assert [e["tool"] for e in events if e["type"] == "step"] == [
            "retrieve",
            "outline",
            "section",
            "section",
            "section",
            "section",
            "render",
        ]
        assert calls == ["outline"] + ["section"] * 4

        doc = next(e for e in events if e["type"] == "document")["document"]
        assert doc["title"] == "Report o prietoku"
        assert doc["filename"] == "report-o-prietoku.pdf"
        assert doc["pages"] >= 1

        verify = next(e for e in events if e["type"] == "verify")
        assert verify["ok"] is True  # 12.4 je v zdroji

        answer = "".join(e["text"] for e in events if e["type"] == "delta")
        assert "Report o prietoku" in answer

        pdf = client.get(doc["url"])
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        assert pdf.headers["content-disposition"].startswith("inline")
        download = client.get(doc["url"] + "?download=true")
        assert download.headers["content-disposition"].startswith("attachment")

        listed = client.get("/api/generated").json()
        assert [d["id"] for d in listed] == [doc["id"]]

        chat_id = next(e for e in events if e["type"] == "sources")["chat_id"]
        msgs = client.get(f"/api/chats/{chat_id}").json()
        assert msgs[1]["document"]["id"] == doc["id"]
        assert msgs[0]["document"] is None


def test_generated_404(client: TestClient) -> None:
    with client:
        assert client.get("/api/generated/neexistuje/pdf").status_code == 404


def test_chat_404_and_validation(client: TestClient) -> None:
    with client:
        assert client.get("/api/chats/neexistuje").status_code == 404
        assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_follow_up_is_rewritten_and_traced(client: TestClient) -> None:
    with client:
        first = _parse_sse(client.post("/api/chat", json={"message": "Aký bol prietok?"}).text)
        chat_id = next(e for e in first if e["type"] == "sources")["chat_id"]
        second = _parse_sse(
            client.post("/api/chat", json={"message": "A pri A-01?", "chat_id": chat_id}).text
        )
        rewrite = next(e for e in second if e["type"] == "rewrite")
        assert rewrite["question"] == "Aký bol prietok vo vzorke A-01?"

        done = next(e for e in second if e["type"] == "done")
        trace = client.get(f"/api/traces/{done['trace_id']}").json()
        names = [s["name"] for s in trace["spans"]]
        assert names[0] == "rag.query"
        assert {"rag.rewrite", "rag.route", "rag.retrieve"} <= set(names)
        root = trace["spans"][0]["attributes"]
        assert root["rag.standalone_question"] == "Aký bol prietok vo vzorke A-01?"
        assert root["rag.mode"] == "lookup"

        msgs = client.get(f"/api/chats/{chat_id}").json()
        assert msgs[-1]["trace_id"] == done["trace_id"]


def test_feedback_roundtrip(client: TestClient) -> None:
    with client:
        events = _parse_sse(client.post("/api/chat", json={"message": "Aký bol prietok?"}).text)
        done = next(e for e in events if e["type"] == "done")
        message_id = done["message_id"]

        assert (
            client.put(
                f"/api/messages/{message_id}/feedback", json={"rating": -1, "comment": "zlé číslo"}
            ).status_code
            == 204
        )
        msgs = client.get(f"/api/chats/{done['chat_id']}").json()
        assert msgs[-1]["rating"] == -1

        traces = client.get("/api/traces").json()
        assert traces[0]["trace_id"] == done["trace_id"]
        assert traces[0]["rating"] == -1
        assert traces[0]["question"] == "Aký bol prietok?"

        exported = client.get("/api/feedback/export").json()
        assert exported[0]["question"] == "Aký bol prietok?"
        assert exported[0]["gold"] == [["vzorka.pdf", 2]]
        assert exported[0]["comment"] == "zlé číslo"

        assert client.delete(f"/api/messages/{message_id}/feedback").status_code == 204
        assert client.get("/api/feedback").json() == []
        # feedback len na odpovede asistenta
        user_msg = client.get(f"/api/chats/{done['chat_id']}").json()[0]["id"]
        assert (
            client.put(f"/api/messages/{user_msg}/feedback", json={"rating": 1}).status_code == 404
        )
        assert (
            client.put(f"/api/messages/{message_id}/feedback", json={"rating": 5}).status_code
            == 422
        )

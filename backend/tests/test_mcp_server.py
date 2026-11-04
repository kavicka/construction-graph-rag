import json
from typing import Any

import pytest
from mcp import Client

from app import db
from app.config import get_settings
from app.mcp_server import mcp
from app.retrieve.orchestrator import AnswerResult
from app.retrieve.pipeline import Source

pytestmark = pytest.mark.anyio


def _src(n: int = 1) -> Source:
    return Source(
        n=n,
        chunk_id=f"d1:t{n}",
        doc_id="d1",
        filename="zmluva.pdf",
        page_from=2,
        page_to=2,
        type="text",
        table_id=None,
        text="Cena diela je 4 860 000 EUR.",
        snippet="Cena diela",
    )


@pytest.fixture
def library() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "zmluva.pdf", "sha-1")
    db.set_document_status("d1", "done", progress=1.0)


def _payload(result: Any) -> Any:
    if result.structured_content is not None:
        data = result.structured_content
        return data.get("result", data) if isinstance(data, dict) else data
    return json.loads(result.content[0].text)


async def test_lists_tools() -> None:
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "list_documents",
        "search_documents",
        "find_connections",
        "ask",
        "research",
        "get_document",
        "generate_document",
    }
    assert tools["search_documents"].annotations.read_only_hint is True
    assert "query" in tools["search_documents"].input_schema["properties"]


async def test_list_and_search(library: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.mcp_server.retrieve_sources", lambda q, k: [_src()])
    async with Client(mcp) as client:
        docs = _payload(await client.call_tool("list_documents", {}))
        hits = _payload(await client.call_tool("search_documents", {"query": "cena", "k": 3}))
    assert docs[0]["filename"] == "zmluva.pdf"
    assert hits[0]["filename"] == "zmluva.pdf"
    assert hits[0]["text"].startswith("Cena diela")


async def test_ask_returns_cited_answer(library: None, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_answer(question: str) -> AnswerResult:
        return AnswerResult(
            mode="lookup", answer="Cena je 4 860 000 EUR [1].", sources=[_src()], trace_id="abc"
        )

    monkeypatch.setattr("app.mcp_server.answer_question", fake_answer)
    async with Client(mcp) as client:
        out = _payload(await client.call_tool("ask", {"question": "Aká je cena?"}))
    assert out["answer"].endswith("[1].")
    assert out["sources"][0]["page_from"] == 2
    assert out["trace_id"] == "abc"


async def test_get_document_unknown(library: None) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("get_document", {"document": "neexistuje.pdf"})
    assert result.is_error


async def test_http_endpoint_mounted() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    for _ in range(2):  # lifespan sa dá spustiť opakovane (nová MCP appka pri každom štarte)
        with TestClient(app, base_url="http://localhost:8000") as client:
            resp = client.post(
                "/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "1"},
                    },
                },
            )
            assert resp.status_code == 200, resp.text
            assert "rag" in resp.text
        with TestClient(app, base_url="http://evil.example") as client:
            assert client.post("/mcp", json={}).status_code == 421  # DNS rebinding ochrana

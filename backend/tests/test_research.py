import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.retrieve import research
from app.retrieve.pipeline import Source
from app.retrieve.research import (
    decompose,
    is_compound,
    is_research_request,
    merge_sources,
    sections_from_markdown,
)
from app.retrieve.router import route_query
from tests.test_chat_api import _parse_sse


def _src(cid: str, filename: str = "z.pdf") -> Source:
    return Source(
        n=0,
        chunk_id=cid,
        doc_id="d",
        filename=filename,
        page_from=1,
        page_to=1,
        type="text",
        table_id=None,
        text=f"text {cid}",
        snippet=f"snip {cid}",
    )


@pytest.mark.parametrize(
    ("question", "compound"),
    [
        ("Kto je stavbyvedúci na Agátovej a aká je zmluvná pokuta?", True),
        ("Aká je cena? A kedy je termín?", True),
        ("What is the price and when is the deadline?", True),
        ("Aký je súvis medzi meraním a kalibráciou?", False),
        ("Aká je cena diela Agátová?", False),
    ],
)
def test_is_compound(question: str, compound: bool) -> None:
    assert is_compound(question) is compound


def test_research_trigger_and_routing() -> None:
    assert is_research_request("/research riziká projektu Agátová")
    assert is_research_request("Preskúmaj všetko o subdodávateľoch")
    assert not is_research_request("Aká je cena diela?")
    assert route_query("Urob rešerš rizík na stavbe Agátová").mode == "research"
    # explicitný dokument má prednosť
    assert route_query("Preskúmaj zmluvy a priprav report").mode == "generate"


def test_decompose_uses_llm_then_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        research,
        "generate_sync",
        lambda *a, **k: json.dumps(
            {"questions": ["Kto je stavbyvedúci Agátová?", "Aká je pokuta Agátová?"]}
        ),
    )
    assert decompose("Kto je stavbyvedúci a aká je pokuta?") == [
        "Kto je stavbyvedúci Agátová?",
        "Aká je pokuta Agátová?",
    ]
    monkeypatch.setattr(research, "generate_sync", lambda *a, **k: "zlý json")
    assert decompose("Kto je stavbyvedúci na Agátovej a aká je pokuta?") == [
        "Kto je stavbyvedúci na Agátovej?",
        "Aká je pokuta?",
    ]


def test_merge_sources_round_robin_dedupe() -> None:
    merged = merge_sources([[_src("a"), _src("b"), _src("c")], [_src("x"), _src("a")]], cap=4)
    assert [s.chunk_id for s in merged] == ["a", "x", "b", "c"]
    assert [s.n for s in merged] == [1, 2, 3, 4]


def test_sections_from_markdown() -> None:
    md = "Úvodná veta.\n## Zhrnutie\nText [1].\n\n## Zistenia\n- bod [2]\n## Prázdna\n"
    assert sections_from_markdown(md) == [
        ("Odpoveď", "Úvodná veta."),
        ("Zhrnutie", "Text [1]."),
        ("Zistenia", "- bod [2]"),
    ]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    def fake_generate(model: str, prompt: str, **kw: object) -> str:
        if kw.get("json_schema"):
            return json.dumps({"questions": ["Aká je cena?", "Aký je termín?", "Kto dozoruje?"]})
        return "Cena je 12.4 EUR [1]."

    async def fake_stream(
        model: str, messages: list[dict[str, str]], **kw: object
    ) -> AsyncIterator[str]:
        for tok in ["## Zhrnutie\n", "Cena je 12.4 EUR [1].\n", "## Súvislosti\nNič [2]."]:
            yield tok

    monkeypatch.setattr("app.retrieve.research.generate_sync", fake_generate)
    monkeypatch.setattr("app.retrieve.orchestrator.chat_stream", fake_stream)
    monkeypatch.setattr(
        "app.retrieve.research.retrieve_sources", lambda q, k: [_src(f"{q[:4]}-1"), _src("shared")]
    )
    return TestClient(app)


def test_research_flow_and_pdf_export(client: TestClient) -> None:
    with client:
        events = _parse_sse(
            client.post("/api/chat", json={"message": "Zhrň projekt", "mode": "research"}).text
        )
        assert events[0] == {"type": "plan", "mode": "research"}
        tools = [e["tool"] for e in events if e["type"] == "step"]
        assert tools.count("subquestion") == 3
        assert tools[-1] == "synthesis"
        sources = next(e for e in events if e["type"] == "sources")["sources"]
        assert len({s["chunk_id"] for s in sources}) == len(sources) == 4  # 3 vlastné + shared
        done = next(e for e in events if e["type"] == "done")

        exported = client.post(f"/api/messages/{done['message_id']}/export")
        assert exported.status_code == 200, exported.text
        doc = exported.json()
        assert doc["title"] == "Zhrň projekt"
        assert doc["sections"] == ["Zhrnutie", "Súvislosti"]
        assert client.get(doc["url"]).content.startswith(b"%PDF")
        msgs = client.get(f"/api/chats/{done['chat_id']}").json()
        assert msgs[-1]["document"]["id"] == doc["id"]
        assert client.post("/api/messages/neexistuje/export").status_code == 404


def test_compound_lookup_retrieves_per_subquestion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.retrieve.research.generate_sync",
        lambda *a, **k: json.dumps({"questions": ["Kto je stavbyvedúci?", "Aká je pokuta?"]}),
    )
    with client:
        events = _parse_sse(
            client.post("/api/chat", json={"message": "Kto je stavbyvedúci a aká je pokuta?"}).text
        )
        step = next(e for e in events if e["type"] == "step")
        assert step == {
            "type": "step",
            "tool": "decompose",
            "summary": "Kto je stavbyvedúci? · Aká je pokuta?",
        }
        sources = next(e for e in events if e["type"] == "sources")["sources"]
        assert {s["chunk_id"] for s in sources} == {"Kto -1", "Aká -1", "shared"}

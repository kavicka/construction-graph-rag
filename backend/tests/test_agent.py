"""Agent loop: tool runtime (exact record queries), source numbering and loop termination."""

import json
from typing import Any

import pytest

from app import db
from app.config import get_settings
from app.llm.ollama_client import ToolCall, ToolTurn
from app.retrieve import agent
from app.retrieve.agent import AGENT_MAX_STEPS, TOOLS, ToolRuntime, _renumber, run_agent
from app.retrieve.pipeline import Source


@pytest.fixture
def meals() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "jedla.pdf", "sha-1")
    db.insert_table("t-obed", "d1", 14, "Oběd varianty", 3, 4, "/tmp/x.md", None)
    db.insert_table("t-snidane", "d1", 5, "Snídaně", 2, 4, "/tmp/y.md", None)
    data = [
        ("t-obed", "Oběd varianty", "Stir-fry", {"kcal": 900, "b": 50}),
        ("t-obed", "Oběd varianty", "Kuřecí rizoto", {"kcal": 829, "b": 74}),
        ("t-obed", "Oběd varianty", "Těstoviny", {"kcal": 820, "b": 55}),
        ("t-snidane", "Snídaně", "Ovesná kaše", {"kcal": 447, "b": 30}),
        ("t-snidane", "Snídaně", "Toasty", {"kcal": 477, "b": 25}),
    ]
    db.replace_document_records(
        "d1",
        [
            {
                "id": f"r{i}",
                "table_id": tid,
                "page_no": 14,
                "category": cat,
                "name": name,
                "attrs_json": json.dumps(attrs),
                "raw_text": name,
            }
            for i, (tid, cat, name, attrs) in enumerate(data)
        ],
    )


def _source(n: int, chunk_id: str, text: str = "text") -> Source:
    return Source(
        n=n,
        chunk_id=chunk_id,
        doc_id="d1",
        filename="smernica.pdf",
        page_from=3,
        page_to=3,
        type="text",
        table_id=None,
        text=text,
        snippet=text,
    )


# --- _renumber -------------------------------------------------------------------------


def test_renumber_dedupes_by_chunk_and_numbers_from_one() -> None:
    out = _renumber([_source(7, "a"), _source(3, "b"), _source(9, "a"), _source(1, "c")])
    assert [(s.n, s.chunk_id) for s in out] == [(1, "a"), (2, "b"), (3, "c")]
    assert out[0].n == 1  # first occurrence wins


def test_renumber_empty() -> None:
    assert _renumber([]) == []


# --- tool schema -----------------------------------------------------------------------


def test_tool_schema_names_match_runtime_handlers(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "retrieve_sources", lambda q, k: [])  # no embedder in this test
    names = {t["function"]["name"] for t in TOOLS}
    assert names == {"query_records", "pick_items", "search_docs"}
    runtime = ToolRuntime()
    for name in names:
        assert not runtime.execute(name, {}).startswith("Neznámy nástroj")


# --- ToolRuntime.query_records ---------------------------------------------------------


def test_query_records_filters_exactly(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute(
        "query_records",
        {
            "constraints": [{"attr": "kcal", "op": "<=", "value": 830}],
            "categories": ["Oběd varianty"],
        },
    )
    assert "Těstoviny" in out and "Kuřecí rizoto" in out
    assert "Stir-fry" not in out  # 900 kcal is over the limit
    assert "Ovesná kaše" not in out  # other category
    assert rt.sources  # records are citable
    assert rt.evidence == [out]


def test_query_records_ignores_malformed_constraints(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute(
        "query_records",
        {
            "constraints": [
                {"attr": "", "op": "<=", "value": 1},  # no attribute
                {"attr": "kcal", "op": "", "value": 1},  # no operator
                {"attr": "kcal", "op": ">=", "value": "850"},  # numeric string is coerced
            ],
            "categories": ["Oběd varianty"],
        },
    )
    assert "Stir-fry" in out
    assert "Těstoviny" not in out  # only the valid >= 850 constraint was applied


def test_query_records_failure_is_reported_not_raised(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute("query_records", {"constraints": [{"attr": "kcal", "op": "<=", "value": "x"}]})
    assert out.startswith("Nástroj query_records zlyhal")
    assert rt.evidence == []  # failures are not evidence for the number self-check


# --- ToolRuntime.pick_items ------------------------------------------------------------


def test_pick_items_exact_combination_within_budget(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute(
        "pick_items",
        {
            "categories": ["Oběd varianty", "Snídaně"],
            "budget_attr": "kcal",
            "budget_max": 1300,
            "maximize": "b",
        },
    )
    assert out.startswith("EXAKTNÝ výber kombinácie")
    # best protein: rizoto (829 kcal, 74 g) + kaše (447 kcal, 30 g) = 1276 kcal
    assert "Kuřecí rizoto" in out and "Ovesná kaše" in out
    assert "kcal=1276" in out
    assert "b=104" in out
    assert len(rt.sources) == 2
    assert rt.evidence == [out]


def test_pick_items_reports_unreachable_budget(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute(
        "pick_items",
        {"categories": ["Oběd varianty", "Snídaně"], "budget_attr": "kcal", "budget_max": 500},
    )
    assert out.startswith("Limit sa NEDÁ splniť")
    assert "Súčet:" in out


def test_pick_items_per_category_limit(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute(
        "pick_items",
        {
            "categories": ["Oběd varianty", "Snídaně"],
            "budget_attr": "kcal",
            "budget_max": 2000,
            "maximize": "b",
            "per_category_max": {"Oběd varianty": 830},
        },
    )
    assert "Stir-fry" not in out  # 900 kcal lunch is excluded by the per-category cap
    assert "Kuřecí rizoto" in out


def test_pick_items_unknown_category(meals: None) -> None:
    rt = ToolRuntime()
    out = rt.execute(
        "pick_items", {"categories": ["Večeře"], "budget_attr": "kcal", "budget_max": 900}
    )
    assert out == "V zadaných kategóriách nie je z čoho vyberať."
    assert rt.sources == []


def test_pick_items_missing_required_argument_is_reported(meals: None) -> None:
    out = ToolRuntime().execute("pick_items", {"categories": ["Snídaně"]})
    assert out.startswith("Nástroj pick_items zlyhal")


# --- ToolRuntime.search_docs / execute -------------------------------------------------


def test_search_docs_formats_numbered_snippets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    def fake_retrieve(query: str, k: int) -> list[Source]:
        calls.append((query, k))
        return [_source(1, "a", "x" * 1000), _source(2, "b", "druhý úsek")]

    monkeypatch.setattr(agent, "retrieve_sources", fake_retrieve)
    rt = ToolRuntime()
    out = rt.execute("search_docs", {"query": "zmluvná pokuta"})
    assert calls == [("zmluvná pokuta", 5)]
    first, second = out.split("\n\n")
    assert first == "[1] (smernica.pdf, str. 3): " + "x" * 600  # snippet capped at 600 chars
    assert second == "[2] (smernica.pdf, str. 3): druhý úsek"
    assert [s.chunk_id for s in rt.sources] == ["a", "b"]


def test_search_docs_without_query_uses_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(agent, "retrieve_sources", lambda q, k: seen.append(q) or [])
    assert ToolRuntime().execute("search_docs", {}) == ""
    assert seen == [""]


def test_execute_unknown_tool_is_not_evidence() -> None:
    rt = ToolRuntime()
    assert rt.execute("rm_rf", {}) == "Neznámy nástroj: rm_rf"
    assert rt.evidence == []


def test_execute_converts_handler_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(query: str, k: int) -> list[Source]:
        raise RuntimeError("index offline")

    monkeypatch.setattr(agent, "retrieve_sources", boom)
    rt = ToolRuntime()
    assert rt.execute("search_docs", {"query": "x"}) == "Nástroj search_docs zlyhal: index offline"
    assert rt.evidence == []


# --- run_agent -------------------------------------------------------------------------


class ScriptedLLM:
    """Replays ToolTurns; records the message history the agent sent each round."""

    def __init__(self, turns: list[ToolTurn]) -> None:
        self.turns = list(turns)
        self.histories: list[list[dict[str, Any]]] = []
        self.models: list[str] = []

    async def __call__(
        self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ToolTurn:
        self.models.append(model)
        self.histories.append([dict(m) for m in messages])
        return self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]


def _call(name: str, **arguments: Any) -> ToolCall:
    return ToolCall(id=f"id-{name}", name=name, arguments=arguments)


def _calls_turn(*calls: ToolCall) -> ToolTurn:
    return ToolTurn(content="", calls=list(calls), message={"role": "assistant", "tool_calls": []})


@pytest.mark.anyio
async def test_run_agent_answers_without_tools(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ScriptedLLM([ToolTurn(content="Hotovo.")])
    monkeypatch.setattr(agent, "chat_with_tools", llm)
    result = await run_agent("Ahoj?")
    assert result.answer == "Hotovo."
    assert result.sources == [] and result.trace == [] and result.evidence == []
    assert len(llm.histories) == 1
    assert llm.models == [get_settings().answer_model]


@pytest.mark.anyio
async def test_run_agent_system_prompt_lists_library_attributes(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.replace_record_attrs("d1", [("val_g", "Hmotnosť", "g"), ("farba", "Farba", None)])
    llm = ScriptedLLM([ToolTurn(content="ok")])
    monkeypatch.setattr(agent, "chat_with_tools", llm)
    await run_agent("Otázka")
    system, user = llm.histories[0]
    assert system["role"] == "system" and user == {"role": "user", "content": "Otázka"}
    assert "kcal (energia)" in system["content"]
    assert "val_g (Hmotnosť, g)" in system["content"]
    assert "farba (Farba)" in system["content"]  # no unit → no trailing comma


@pytest.mark.anyio
async def test_run_agent_tool_round_then_answer(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm = ScriptedLLM(
        [
            _calls_turn(
                _call(
                    "pick_items",
                    categories=["Oběd varianty", "Snídaně"],
                    budget_attr="kcal",
                    budget_max=1300,
                    maximize="b",
                )
            ),
            ToolTurn(content="Vyber rizoto a kaši."),
        ]
    )
    monkeypatch.setattr(agent, "chat_with_tools", llm)
    result = await run_agent("Čo si dať do 1300 kcal?")

    assert result.answer == "Vyber rizoto a kaši."
    assert [t["tool"] for t in result.trace] == ["pick_items"]
    assert "budget_max" in result.trace[0]["args"]
    assert len(result.evidence) == 1 and "Kuřecí rizoto" in result.evidence[0]
    assert [s.n for s in result.sources] == [1, 2]
    # second round sees: system, user, assistant tool-call message, tool result
    second = llm.histories[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert second[3]["tool_name"] == "pick_items"
    assert second[3]["content"] == result.evidence[0]


@pytest.mark.anyio
async def test_run_agent_multiple_calls_in_one_turn_share_sources(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    same_query = {
        "constraints": [{"attr": "kcal", "op": "<=", "value": 830}],
        "categories": ["Oběd varianty"],
    }
    llm = ScriptedLLM(
        [
            _calls_turn(_call("query_records", **same_query), _call("query_records", **same_query)),
            ToolTurn(content="Dve jedlá."),
        ]
    )
    monkeypatch.setattr(agent, "chat_with_tools", llm)
    result = await run_agent("Obed do 830 kcal?")
    assert len(result.trace) == 2
    # both calls return the same records → deduplicated and numbered 1..n without gaps
    assert [s.n for s in result.sources] == list(range(1, len(result.sources) + 1))
    assert len({s.chunk_id for s in result.sources}) == len(result.sources)
    assert len(llm.histories[1]) == 2 + 1 + 2  # system, user, assistant, 2 tool results


@pytest.mark.anyio
async def test_run_agent_stops_after_max_steps(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a model that never stops calling tools must not loop forever
    llm = ScriptedLLM([_calls_turn(_call("nonexistent_tool"))])
    monkeypatch.setattr(agent, "chat_with_tools", llm)
    result = await run_agent("Zacykli sa")
    assert len(llm.histories) == AGENT_MAX_STEPS
    assert len(result.trace) == AGENT_MAX_STEPS
    assert "Prekročený limit krokov" in result.answer
    assert result.evidence == []  # unknown tool never counts as evidence


@pytest.mark.anyio
async def test_run_agent_empty_final_content_gets_fallback_text(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "chat_with_tools", ScriptedLLM([ToolTurn(content="")]))
    result = await run_agent("?")
    assert result.answer == "Nepodarilo sa zostaviť odpoveď."


@pytest.mark.anyio
async def test_run_agent_trace_args_are_truncated(
    meals: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent, "retrieve_sources", lambda q, k: [])
    llm = ScriptedLLM([_calls_turn(_call("search_docs", query="ž" * 500)), ToolTurn(content="nič")])
    monkeypatch.setattr(agent, "chat_with_tools", llm)
    result = await run_agent("dlhý dotaz")
    assert len(result.trace[0]["args"]) == 200
    assert "\\u" not in result.trace[0]["args"]  # non-ASCII kept readable for the UI


@pytest.mark.anyio
async def test_run_agent_llm_error_propagates(meals: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm.ollama_client import OllamaError

    async def boom(*a: Any, **k: Any) -> ToolTurn:
        raise OllamaError("agent chat zlyhal")

    monkeypatch.setattr(agent, "chat_with_tools", boom)
    with pytest.raises(OllamaError):
        await run_agent("?")

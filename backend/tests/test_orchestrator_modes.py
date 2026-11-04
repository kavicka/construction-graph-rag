"""Orchestrator mode handlers driven directly through run_query: compose (exact + agent
fallback), filter, global, generate, handler selection and the final verification step.

Every LLM / retrieval / document-rendering call is replaced by a fake, so the tests only
exercise the orchestration: event order, step labels, post-processing and verification."""

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app import db
from app.config import get_settings
from app.retrieve import orchestrator
from app.retrieve.agent import AgentResult
from app.retrieve.orchestrator import answer_question, run_query, strip_think
from app.retrieve.pipeline import Source
from app.retrieve.router import QueryPlan

Event = dict[str, Any]


def _source(n: int, text: str = "Prietok bol 15 l/min.") -> Source:
    return Source(
        n=n,
        chunk_id=f"d1:t{n}",
        doc_id="d1",
        filename="vzorka.pdf",
        page_from=1,
        page_to=1,
        type="text",
        table_id=None,
        text=text,
        snippet=text,
    )


@pytest.fixture(autouse=True)
def isolated_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No firma data, no sentence verification (needs the embedder), a real empty DB."""
    get_settings().ensure_dirs()
    db.init_db()
    monkeypatch.setattr(orchestrator, "answer_firma", lambda question, viewer: None)
    monkeypatch.setattr(orchestrator, "verify_sentences", lambda answer, sources: [])


def plan_as(monkeypatch: pytest.MonkeyPatch, plan: QueryPlan) -> None:
    monkeypatch.setattr(orchestrator, "route_query", lambda question: plan)


async def events_of(question: str = "Otázka?", **kwargs: Any) -> list[Event]:
    return [e async for e in run_query(question, **kwargs)]


def of_type(events: list[Event], kind: str) -> list[Event]:
    return [e for e in events if e["type"] == kind]


def final_of(events: list[Event]) -> Event:
    assert events[-1]["type"] == "final"
    return events[-1]


def shape(events: list[Event]) -> list[str]:
    """Event types in order; consecutive deltas collapse (the secret filter re-chunks text)."""
    out: list[str] = []
    for e in events:
        if not (e["type"] == "delta" and out and out[-1] == "delta"):
            out.append(e["type"])
    return out


def streamed_text(events: list[Event]) -> str:
    return "".join(e["text"] for e in of_type(events, "delta"))


def tools_of(events: list[Event]) -> list[str]:
    return [e["tool"] for e in of_type(events, "step")]


def fake_stream(*tokens: str) -> Any:
    calls: list[dict[str, Any]] = []

    async def stream(model: str, messages: list[dict[str, str]], **kw: Any) -> AsyncIterator[str]:
        calls.append({"model": model, "messages": messages, **kw})
        for t in tokens:
            yield t

    stream.calls = calls  # type: ignore[attr-defined]
    return stream


# --- compose ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_compose_exact_selection_path(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="compose"))
    monkeypatch.setattr(orchestrator, "known_categories", lambda: ["Obed", "Raňajky"])
    seen: list[Any] = []

    def fake_compose(question: str, categories: list[str]) -> tuple[str, list[Source], list[str]]:
        seen.append(categories)
        return "Obed + raňajky spolu 1276 kcal [1].", [_source(1)], ["kcal=1276"]

    monkeypatch.setattr(orchestrator, "run_compose", fake_compose)
    events = await events_of("Obed a raňajky do 1300 kcal")

    assert seen == [["Obed", "Raňajky"]]
    assert shape(events) == ["plan", "step", "step", "sources", "delta", "verify", "final"]
    assert streamed_text(events) == final_of(events)["answer"]  # no "replace" needed
    assert tools_of(events) == ["plan", "pick_items"]
    final = final_of(events)
    assert final["mode"] == "compose"
    assert final["answer"] == "Obed + raňajky spolu 1276 kcal [1]."
    assert of_type(events, "verify")[0]["ok"] is True  # 1276 is in the tool evidence
    assert of_type(events, "verify")[0]["sentences"] == []  # deterministic path: numbers only


@pytest.mark.anyio
async def test_compose_falls_back_to_agent_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="compose"))
    monkeypatch.setattr(orchestrator, "known_categories", lambda: [])
    monkeypatch.setattr(orchestrator, "run_compose", lambda question, categories: None)

    async def fake_agent(question: str) -> AgentResult:
        return AgentResult(
            answer="Zober rizoto, spolu 829 kcal.",
            sources=[_source(1)],
            trace=[{"tool": "query_records", "args": '{"constraints": []}'}],
            evidence=["Rizoto kcal=829"],
        )

    monkeypatch.setattr(orchestrator, "run_agent", fake_agent)
    events = await events_of("Zober obed")

    assert tools_of(events) == ["plan", "agent", "query_records"]
    steps = of_type(events, "step")
    assert steps[1]["summary"] == "plán neistý — agent slučka"
    assert steps[2]["summary"] == '{"constraints": []}'
    assert of_type(events, "sources")[0]["sources"][0]["chunk_id"] == "d1:t1"
    assert final_of(events)["answer"] == "Zober rizoto, spolu 829 kcal."
    assert of_type(events, "verify")[0]["ok"] is True


@pytest.mark.anyio
async def test_agent_answer_with_invented_number_is_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_as(monkeypatch, QueryPlan(mode="compose"))
    monkeypatch.setattr(orchestrator, "known_categories", lambda: [])
    monkeypatch.setattr(orchestrator, "run_compose", lambda question, categories: None)

    async def fake_agent(question: str) -> AgentResult:
        return AgentResult(answer="Spolu 4321 kcal.", sources=[], evidence=["Rizoto kcal=829"])

    monkeypatch.setattr(orchestrator, "run_agent", fake_agent)
    verify = of_type(await events_of("Zober obed"), "verify")[0]
    assert verify["ok"] is False
    assert verify["issues"] == ["4321"]


@pytest.mark.anyio
async def test_compose_without_agent_mode_answers_as_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_AGENT_MODE", "false")
    get_settings.cache_clear()
    plan_as(monkeypatch, QueryPlan(mode="compose"))
    stream = fake_stream("Podľa ", "podkladov [1].")
    monkeypatch.setattr(orchestrator, "chat_stream", stream)
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, *a, **k: [_source(1)])
    monkeypatch.setattr(
        orchestrator,
        "run_compose",
        lambda *a: pytest.fail("compose must not run without agent mode"),
    )
    events = await events_of("Obed a raňajky do 1300 kcal")
    assert len(stream.calls) == 1  # went through the classic RAG answer
    assert final_of(events)["mode"] == "compose"  # the plan is still reported as routed
    assert final_of(events)["answer"] == "Podľa podkladov [1]."


@pytest.mark.anyio
async def test_generate_without_doc_type_answers_as_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="generate", doc_type=None))
    stream = fake_stream("Odpoveď [1].")
    monkeypatch.setattr(orchestrator, "chat_stream", stream)
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, *a, **k: [_source(1)])
    events = await events_of("Vytvor niečo")
    assert len(stream.calls) == 1
    assert of_type(events, "document") == []


# --- filter ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_filter_mode_never_calls_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = QueryPlan(mode="filter")
    plan_as(monkeypatch, plan)
    hit = SimpleNamespace(name="Těstoviny")
    monkeypatch.setattr(orchestrator, "run_filter", lambda p: ([hit], []))
    monkeypatch.setattr(
        orchestrator,
        "build_filter_context",
        lambda p, matched, nearest: ("kontext 820", [_source(1)]),
    )
    monkeypatch.setattr(
        orchestrator,
        "format_filter_answer",
        lambda p, m, n, s: "Nájdené: Těstoviny (820 kcal) [1].",
    )
    monkeypatch.setattr(
        orchestrator, "chat_stream", lambda *a, **k: pytest.fail("filter must not call the LLM")
    )
    events = await events_of("jedlo do 830 kcal")
    assert shape(events) == ["plan", "sources", "delta", "verify", "final"]
    assert final_of(events)["answer"] == "Nájdené: Těstoviny (820 kcal) [1]."
    assert of_type(events, "verify")[0]["ok"] is True  # 820 comes from the filter context


# --- global ----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_global_mode_summarises_themes_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="global"))
    summarised: list[str] = []
    with_summary = SimpleNamespace(
        title="Zmluvy", summary="Zmluvné pokuty 0,05 %.", concepts=["pokuta"]
    )
    without = SimpleNamespace(
        title="Betón", summary=None, concepts=["C25", "C30", "cement", "voda", "x"]
    )

    def summarize(theme: Any) -> None:
        summarised.append(theme.title)
        theme.summary = "Betón: pevnostné triedy C25 a C30."

    monkeypatch.setattr(orchestrator, "detect_communities", lambda: ["c1", "c2"])
    monkeypatch.setattr(
        orchestrator, "rank_communities", lambda q, comms, k: [with_summary, without]
    )
    monkeypatch.setattr(orchestrator, "summarize_community", summarize)
    monkeypatch.setattr(
        orchestrator,
        "global_context",
        lambda q, top: ([_source(1, "Zmluvné pokuty 0,05 %.")], [{"role": "user", "content": q}]),
    )
    stream = fake_stream("Hlavné témy: zmluvy a betón.")
    monkeypatch.setattr(orchestrator, "chat_stream", stream)

    events = await events_of("Aké sú hlavné témy knižnice?")

    assert summarised == ["Betón"]  # only the theme without a stored summary is summarised
    assert tools_of(events) == ["communities", "summarize", "themes"]
    steps = of_type(events, "step")
    assert steps[1]["summary"] == "C25, C30, cement, voda"  # first four concepts
    assert steps[2]["summary"] == "Zmluvy · Betón"
    assert stream.calls[0]["num_ctx"] == 12288
    final = final_of(events)
    assert final["mode"] == "global" and final["answer"] == "Hlavné témy: zmluvy a betón."
    assert final["verification"]["sentences"] == []  # global answers cite themes, not sentences


# --- generate --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_generate_mode_writes_sections_and_returns_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_as(monkeypatch, QueryPlan(mode="generate", doc_type="offer", lang="sk"))
    sources = [_source(1), _source(2)]
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, k: sources)
    outline = SimpleNamespace(title="Cenová ponuka", headings=["Úvod", "Cena"])
    monkeypatch.setattr(orchestrator, "plan_outline", lambda q, dt, lang, src: outline)
    monkeypatch.setattr(orchestrator, "load_company", lambda: {"name": "Firma"})
    written: list[str] = []

    def write(question: str, heading: str, *rest: Any) -> str:
        written.append(heading)
        return f"Text sekcie {heading} [1] a [1,2]."

    monkeypatch.setattr(orchestrator, "write_section", write)
    monkeypatch.setattr(
        orchestrator, "build_content", lambda o, dt, lang, bodies: {"bodies": bodies}
    )
    saved: dict[str, Any] = {}

    def save(content: Any, company: Any, src: list[Source], chat_id: str | None) -> Any:
        saved.update(content=content, chat_id=chat_id)
        return SimpleNamespace(to_json=lambda: {"id": "gen1", "title": "Cenová ponuka"})

    monkeypatch.setattr(orchestrator, "save_document", save)
    monkeypatch.setattr(
        orchestrator, "summary_message", lambda generated, lang, cited: f"Hotovo, citácie: {cited}."
    )

    events = await events_of("Vytvor cenovú ponuku", chat_id="chat-1")

    assert written == ["Úvod", "Cena"]
    assert tools_of(events) == ["retrieve", "outline", "section", "section", "render"]
    sections = [e["summary"] for e in of_type(events, "step") if e["tool"] == "section"]
    assert sections == ["1/2 Úvod", "2/2 Cena"]
    assert saved["chat_id"] == "chat-1"
    assert len(saved["content"]["bodies"]) == 2
    assert of_type(events, "document")[0]["document"]["id"] == "gen1"
    final = final_of(events)
    assert final["document"]["id"] == "gen1"
    assert final["answer"] == "Hotovo, citácie: 2."  # sources 1 and 2 are both cited


@pytest.mark.anyio
async def test_generate_redacts_secrets_in_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="generate", doc_type="memo", lang="sk"))
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, k: [_source(1)])
    monkeypatch.setattr(
        orchestrator, "plan_outline", lambda *a: SimpleNamespace(title="Memo", headings=["Prístup"])
    )
    monkeypatch.setattr(orchestrator, "load_company", lambda: {})
    monkeypatch.setattr(
        orchestrator, "write_section", lambda *a: "Použi kľúč sk-abcdefghijklmnopqrstuvwx1234 [1]."
    )
    captured: dict[str, Any] = {}

    def build(o: Any, dt: Any, lang: Any, bodies: list[str]) -> Any:
        captured["bodies"] = bodies
        return bodies

    monkeypatch.setattr(orchestrator, "build_content", build)
    monkeypatch.setattr(
        orchestrator, "save_document", lambda *a: SimpleNamespace(to_json=lambda: {"id": "g"})
    )
    monkeypatch.setattr(orchestrator, "summary_message", lambda *a: "Hotovo.")
    await events_of("Vytvor memo")
    assert "sk-abcdefghijklmnopqrstuvwx1234" not in captured["bodies"][0]


# --- firma / history / answer_question -------------------------------------------------


@pytest.mark.anyio
async def test_firma_answer_is_a_fixed_reply_without_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        orchestrator,
        "answer_firma",
        lambda question, viewer: SimpleNamespace(
            answer="Jozef odpracoval 8 h.", steps=[{"tool": "firma", "summary": "hodiny"}]
        ),
    )
    monkeypatch.setattr(
        orchestrator, "route_query", lambda q: pytest.fail("firma answers skip the router")
    )
    events = await events_of("Koľko odpracoval Jozef?")
    assert shape(events) == ["plan", "step", "sources", "delta", "verify", "final"]
    assert events[0] == {"type": "plan", "mode": "firma"}
    assert of_type(events, "sources")[0]["sources"] == []
    assert final_of(events)["answer"] == "Jozef odpracoval 8 h."


@pytest.mark.anyio
async def test_research_mode_skips_firma_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        orchestrator, "answer_firma", lambda q, v: pytest.fail("research must not read firma data")
    )
    seen: list[str] = []

    async def fake_research(turn: Any) -> AsyncIterator[Event]:
        seen.append(turn.plan.mode)
        turn.answer_parts.append("Správa.")
        yield {"type": "delta", "text": "Správa."}

    monkeypatch.setitem(orchestrator._MODE_HANDLERS, "research", fake_research)
    events = await events_of("Zhrň projekt", mode="research")
    assert seen == ["research"]
    assert events[0] == {"type": "plan", "mode": "research"}


@pytest.mark.anyio
async def test_history_rewrite_event_and_question_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orchestrator, "needs_rewrite", lambda q, h: True)
    monkeypatch.setattr(
        orchestrator, "rewrite_question", lambda q, h: "Aký bol prietok vo vzorke A-01?"
    )
    routed: list[str] = []

    def route(question: str) -> QueryPlan:
        routed.append(question)
        return QueryPlan(mode="lookup")

    monkeypatch.setattr(orchestrator, "route_query", route)
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, *a, **k: [_source(1)])
    monkeypatch.setattr(orchestrator, "chat_stream", fake_stream("Prietok bol 15 l/min [1]."))
    events = await events_of("A čo prietok?", history=[{"role": "user", "content": "Vzorka A-01"}])
    assert events[0] == {"type": "rewrite", "question": "Aký bol prietok vo vzorke A-01?"}
    assert routed == ["Aký bol prietok vo vzorke A-01?"]


@pytest.mark.anyio
async def test_unchanged_rewrite_emits_no_event(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "needs_rewrite", lambda q, h: True)
    monkeypatch.setattr(orchestrator, "rewrite_question", lambda q, h: q)
    plan_as(monkeypatch, QueryPlan(mode="lookup"))
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, *a, **k: [_source(1)])
    monkeypatch.setattr(orchestrator, "chat_stream", fake_stream("Odpoveď [1]."))
    events = await events_of("Samostatná otázka?", history=[{"role": "user", "content": "x"}])
    assert of_type(events, "rewrite") == []


@pytest.mark.anyio
async def test_answer_question_collects_steps_and_final(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="compose"))
    monkeypatch.setattr(orchestrator, "known_categories", lambda: [])
    monkeypatch.setattr(
        orchestrator,
        "run_compose",
        lambda q, c: ("Spolu 1276 kcal.", [_source(1)], ["kcal=1276"]),
    )
    result = await answer_question("Obed a raňajky do 1300 kcal")
    assert result.mode == "compose"
    assert result.answer == "Spolu 1276 kcal."
    assert [s["tool"] for s in result.steps] == ["plan", "pick_items"]
    assert result.verify_issues == []
    assert len(result.sources) == 1
    assert result.trace_id  # the root span id is attached to the final event


@pytest.mark.anyio
async def test_answer_question_without_final_event_is_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(question: str, **kw: Any) -> AsyncIterator[Event]:
        yield {"type": "plan", "mode": "lookup"}

    monkeypatch.setattr(orchestrator, "run_query", broken)
    with pytest.raises(RuntimeError, match="final"):
        await answer_question("?")


@pytest.mark.anyio
async def test_handler_error_propagates_and_closes_the_root_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_as(monkeypatch, QueryPlan(mode="lookup"))

    def boom(q: str, *a: Any, **k: Any) -> list[Source]:
        raise RuntimeError("index offline")

    monkeypatch.setattr(orchestrator, "retrieve_sources", boom)
    with pytest.raises(RuntimeError, match="index offline"):
        await events_of("Otázka?")


# --- strip_think -----------------------------------------------------------------------


async def _tokens(*parts: str) -> AsyncIterator[str]:
    for p in parts:
        yield p


async def _stripped(*parts: str) -> str:
    return "".join([t async for t in strip_think(_tokens(*parts))])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: strip_think sets skipping=True and `continue`s without looking for </think> in the "
        "same token, so a whole '<think>…</think>answer' arriving in ONE chunk is swallowed "
        "and the answer is lost (orchestrator.py strip_think, checking branch)."
    ),
)
@pytest.mark.anyio
async def test_strip_think_removes_block_in_one_token() -> None:
    assert await _stripped("<think>úvaha</think>\nOdpoveď") == "Odpoveď"


@pytest.mark.anyio
async def test_strip_think_removes_block_split_over_tokens() -> None:
    assert (
        await _stripped("<thi", "nk>dlhá ", "úvaha</th", "ink>\n\n", "Odpoveď ", "[1]")
        == "Odpoveď [1]"
    )


@pytest.mark.anyio
async def test_strip_think_passes_plain_text_through_untouched() -> None:
    assert await _stripped("Prietok ", "bol 15 ", "l/min.") == "Prietok bol 15 l/min."


@pytest.mark.anyio
async def test_strip_think_only_strips_a_leading_block() -> None:
    text = "Odpoveď s <think> uprostred"
    assert await _stripped(text) == text


@pytest.mark.anyio
async def test_strip_think_short_stream_is_flushed() -> None:
    assert await _stripped("<th") == "<th"  # looked like a tag start, but the stream ended
    assert await _stripped("") == ""


@pytest.mark.anyio
async def test_strip_think_unterminated_block_yields_nothing() -> None:
    assert await _stripped("<think>model sa zasekol bez uzavretia") == ""


@pytest.mark.anyio
async def test_strip_think_leading_whitespace_before_tag() -> None:
    assert await _stripped("  <think>x", "</think>Ahoj") == "Ahoj"


# --- scope check for lookups -----------------------------------------------------------


def _snapshot_with(site: str = "Rovinka", worker: str = "Jozef Mikuš") -> Any:
    from app.workforce.qa.data import SiteRef, Snapshot, WorkerRef

    return Snapshot(
        workers=[WorkerRef("w1", worker, True)], sites=[SiteRef("s1", site, True)], tasks=[]
    )


def test_in_scope_when_question_mentions_the_library(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "mentions_library", lambda q: True)
    monkeypatch.setattr(
        orchestrator.qa_data, "snapshot", lambda: pytest.fail("no need to load firma data")
    )
    assert orchestrator._in_scope("cokoľvek") is True


def test_in_scope_when_question_names_a_site_or_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrator, "mentions_library", lambda q: False)
    monkeypatch.setattr(orchestrator.qa_data, "snapshot", _snapshot_with)
    monkeypatch.setattr(
        orchestrator, "classify_scope", lambda q, titles: pytest.fail("classifier not needed")
    )
    assert orchestrator._in_scope("Čo sa robí na Rovinke?") is True
    assert orchestrator._in_scope("Kde dnes pracuje Jozef Mikuš?") is True


def test_in_scope_falls_back_to_classifier_with_visible_titles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db.insert_document("d1", "Zmluva o dielo.pdf", "sha-1")
    db.insert_document("d2", "Skryté financie.pdf", "sha-2")
    monkeypatch.setattr(orchestrator, "mentions_library", lambda q: False)
    monkeypatch.setattr(orchestrator.qa_data, "snapshot", _snapshot_with)
    monkeypatch.setattr(orchestrator, "allowed_doc_ids", lambda: {"d1"})
    seen: list[list[str]] = []

    def classify(question: str, titles: list[str]) -> str:
        seen.append(titles)
        return "offtopic"

    monkeypatch.setattr(orchestrator, "classify_scope", classify)
    assert orchestrator._in_scope("Koľko gólov dal Messi?") is False
    assert seen == [["Zmluva o dielo"]]  # extension stripped, hidden documents not disclosed


@pytest.mark.anyio
async def test_offtopic_lookup_is_refused_without_calling_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_as(monkeypatch, QueryPlan(mode="lookup"))
    monkeypatch.setattr(orchestrator, "_in_scope", lambda q: False)
    monkeypatch.setattr(
        orchestrator, "retrieve_sources", lambda *a, **k: pytest.fail("must not retrieve")
    )
    events = await events_of("Koľko gólov dal Messi?")
    assert events[0] == {"type": "plan", "mode": "lookup"}
    assert any(e["type"] == "step" and e["tool"] == "guard" for e in events)
    assert final_of(events)["sources"] == []


@pytest.mark.anyio
async def test_flagged_sources_emit_a_guard_step(monkeypatch: pytest.MonkeyPatch) -> None:
    plan_as(monkeypatch, QueryPlan(mode="lookup"))
    flagged = _source(2)
    flagged.flagged = True
    monkeypatch.setattr(orchestrator, "retrieve_sources", lambda q, *a, **k: [_source(1), flagged])
    monkeypatch.setattr(orchestrator, "chat_stream", fake_stream("Odpoveď [1]."))
    events = await events_of("Čo hovorí zmluva?")
    guard = [e for e in of_type(events, "step") if e["tool"] == "guard"]
    assert len(guard) == 1 and "[2]" in guard[0]["summary"]

"""Jedna otázka → prúd udalostí (plan, step, sources, delta, document, verify, final).

Spoločné jadro pre chat (SSE), eval, MCP nástroj `ask` a tracing — každý spotrebiteľ
si udalosti formátuje po svojom. Udalosti sú čisté dicty, bez chat_id a perzistencie.
"""

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool
from opentelemetry.context import Context
from opentelemetry.trace import Status, StatusCode

from app import db
from app.auth import allowed_doc_ids
from app.company import load_company
from app.config import Settings, get_settings
from app.docgen.generate import (
    build_content,
    plan_outline,
    save_document,
    summary_message,
    write_section,
)
from app.docgen.render import CITE_RE
from app.graph.communities import (
    detect_communities,
    global_context,
    rank_communities,
    summarize_community,
)
from app.llm.ollama_client import chat_stream
from app.obs.tracing import set_attrs, span, start_root, trace_id_of
from app.retrieve.agent import run_agent
from app.retrieve.compose import run_compose
from app.retrieve.injection import sanitize_answer
from app.retrieve.memory import needs_rewrite, rewrite_question
from app.retrieve.pipeline import Source, build_messages, retrieve_sources
from app.retrieve.records_query import build_filter_context, format_filter_answer, run_filter
from app.retrieve.research import (
    answer_subquestion,
    decompose,
    is_compound,
    merge_sources,
    retrieve_for,
    subquestion_event,
    synthesis_messages,
)
from app.retrieve.router import QueryPlan, known_categories, mentions_library, route_query
from app.retrieve.scope_guard import (
    ScopeDecision,
    check_question,
    classify_scope,
    not_found_reply,
    reply_for,
)
from app.retrieve.secrets import SecretStreamFilter, redact_secrets
from app.retrieve.selfcheck import check_numbers
from app.retrieve.verify import is_refusal_answer, split_sentences, verify_sentences
from app.workforce.qa import data as qa_data
from app.workforce.qa.access import viewer_from_scope
from app.workforce.qa.engine import answer_firma
from app.workforce.qa.entities import match_sites, match_workers

logger = logging.getLogger(__name__)

Event = dict[str, Any]


async def strip_think(stream: AsyncIterator[str]) -> AsyncIterator[str]:
    """Odfiltruje úvodný <think>…</think> blok, ak by ho model predsa poslal
    (thinking varianty qwen3 ho leakujú do contentu)."""
    head = ""
    checking = True
    skipping = False
    async for tok in stream:
        if skipping:
            head += tok
            if "</think>" in head:
                rest = head.split("</think>", 1)[1].lstrip("\n")
                skipping = False
                checking = False
                head = ""
                if rest:
                    yield rest
            continue
        if checking:
            head += tok
            stripped = head.lstrip()
            if stripped.startswith("<think>"):
                skipping = True
                continue
            if len(stripped) >= 7 or not "<think>".startswith(stripped):
                checking = False
                buffered, head = head, ""
                yield buffered
            continue
        yield tok
    if head and not skipping:
        yield head


async def _traced[T](
    name: str, parent: Context, fn: Callable[..., T], *args: Any, **attrs: Any
) -> T:
    """Sync krok v threadpoole ako child span — vnorené spany (LLM, retrieval) sa naň
    napoja, lebo anyio kopíruje contextvars do threadu."""
    with span(name, parent=parent, **attrs):
        return await run_in_threadpool(fn, *args)


def _sources_event(sources: list[Source]) -> Event:
    return {"type": "sources", "sources": [s.to_json() for s in sources]}


async def _fixed(mode: str, text: str, steps: list[dict[str, str]]) -> AsyncIterator[Event]:
    """Odpoveď, ktorú poskladal kód (firemné údaje, odmietnutie) — bez LLM a bez zdrojov.
    Prázdna udalosť `sources` je nutná: UI si z nej berie chat_id."""
    yield {"type": "plan", "mode": mode}
    for step in steps:
        yield {"type": "step", **step}
    yield _sources_event([])
    yield {"type": "delta", "text": text}
    yield {"type": "verify", "ok": True, "issues": [], "sentences": []}
    yield {
        "type": "final",
        "mode": mode,
        "answer": text,
        "sources": [],
        "document": None,
        "trace_id": None,
        "verification": {"issues": [], "sentences": []},
    }


def _in_scope(question: str) -> bool:
    """Lookup otázka bez signálu z knižnice ani firmy → rozhodne malý LLM klasifikátor."""
    if mentions_library(question):
        return True
    snap = qa_data.snapshot()  # stavba či pracovník v otázke = určite firemná téma
    if match_sites(question, snap.sites) or match_workers(question, snap.workers).workers:
        return True
    allowed = allowed_doc_ids()
    titles = [
        d["filename"].rsplit(".", 1)[0]
        for d in db.list_documents()
        if allowed is None or d["id"] in allowed
    ]
    return classify_scope(question, titles) == "ok"


async def run_query(
    question: str,
    *,
    chat_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
    mode: str | None = None,
) -> AsyncIterator[Event]:
    """Posledná udalosť je vždy {"type": "final", answer, sources, document, trace_id}.
    `history` = predchádzajúce správy chatu (role, content) pre doplňujúce otázky."""
    root, ctx = start_root("rag.query", rag__question=question, rag__chat_id=chat_id)
    try:
        # brána kontroluje pôvodnú otázku — prepis z histórie by žiadosť o heslo mohol
        # preformulovať tak, že ju vzory nezachytia
        blocked = get_settings().scope_guard and check_question(question).blocked
        if history and not blocked and needs_rewrite(question, history):
            standalone = await _traced(
                "rag.rewrite",
                ctx,
                rewrite_question,
                question,
                history,
                rag__history=len(history),
            )
            if standalone != question:
                set_attrs(root, rag__standalone_question=standalone)
                yield {"type": "rewrite", "question": standalone}
                question = standalone
        # tajomstvá sa redigujú už v streame — nesmú preblysnúť ani na zlomok sekundy
        stream_filter = SecretStreamFilter()
        streamed: list[str] = []
        async for event in _run(question, chat_id, ctx, mode):
            if event["type"] == "delta":
                if safe := stream_filter.feed(event["text"]):
                    streamed.append(safe)
                    yield {"type": "delta", "text": safe}
                continue
            if event["type"] in ("verify", "final") and (rest := stream_filter.flush()):
                streamed.append(rest)
                yield {"type": "delta", "text": rest}
            if event["type"] == "final":
                if event["answer"] != "".join(streamed):
                    # finálna odpoveď prešla redakciou / očistou → UI ju vymení celú
                    yield {"type": "replace", "text": event["answer"]}
                event["trace_id"] = trace_id_of(root)
                set_attrs(
                    root,
                    rag__mode=event["mode"],
                    rag__answer=event["answer"],
                    rag__sources=len(event["sources"]),
                )
            elif event["type"] == "verify":
                weak = [x for x in event["sentences"] if x["status"] != "supported"]
                set_attrs(
                    root,
                    rag__verify_ok=event["ok"],
                    rag__sentences=len(event["sentences"]),
                    rag__weak_sentences=len(weak),
                )
            yield event
    except Exception as exc:
        root.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
        root.record_exception(exc)
        raise
    finally:
        root.end()


@dataclass
class _Turn:
    """One routed question on its way through a mode handler.

    A handler streams its events and fills in the answer fields; `_run` then applies the
    shared post-processing (secret redaction, refusal cleanup, verification)."""

    question: str
    plan: QueryPlan
    chat_id: str | None
    ctx: Context
    settings: Settings
    sources: list[Source] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    answer_parts: list[str] = field(default_factory=list)
    document: dict[str, Any] | None = None
    verify_text: str | None = None  # None → overuje sa samotná odpoveď
    finished: bool = False  # the handler already sent a complete fixed reply (refusal)


ModeHandler = Callable[[_Turn], AsyncIterator[Event]]


async def _stream_answer(turn: _Turn, stream: AsyncIterator[str]) -> AsyncIterator[Event]:
    """LLM tokens → delta events, collected into the answer."""
    async for token in strip_think(stream):
        turn.answer_parts.append(token)
        yield {"type": "delta", "text": token}


async def _generate(turn: _Turn) -> AsyncIterator[Event]:
    """Branded PDF document written section by section from the retrieved sources."""
    question, ctx = turn.question, turn.ctx
    doc_type, lang = turn.plan.doc_type, turn.plan.lang
    assert doc_type is not None  # _handler_for routes generate without a type to lookup
    yield {"type": "step", "tool": "retrieve", "summary": "hľadám podklady…"}
    sources = await _traced("rag.retrieve", ctx, retrieve_sources, question, 10)
    turn.sources = sources
    yield _sources_event(sources)
    outline = await _traced("docgen.outline", ctx, plan_outline, question, doc_type, lang, sources)
    yield {"type": "step", "tool": "outline", "summary": outline.title}
    company = await run_in_threadpool(load_company)
    bodies: list[str] = []
    for i, heading in enumerate(outline.headings, start=1):
        yield {
            "type": "step",
            "tool": "section",
            "summary": f"{i}/{len(outline.headings)} {heading}",
        }
        bodies.append(
            redact_secrets(
                await _traced(
                    "docgen.section",
                    ctx,
                    write_section,
                    question,
                    heading,
                    outline,
                    doc_type,
                    lang,
                    sources,
                    company,
                    rag__heading=heading,
                )
            )
        )
    yield {"type": "step", "tool": "render", "summary": "renderujem PDF…"}
    content = build_content(outline, doc_type, lang, bodies)
    generated = await _traced(
        "docgen.render", ctx, save_document, content, company, sources, turn.chat_id
    )
    turn.document = generated.to_json()
    yield {"type": "document", "document": turn.document}

    cited = {int(n) for b in bodies for m in CITE_RE.finditer(b) for n in m.group(1).split(",")}
    answer_text = summary_message(generated, lang, len(cited & {s.n for s in sources}))
    turn.answer_parts.append(answer_text)
    yield {"type": "delta", "text": answer_text}
    turn.evidence = [*(s.text for s in sources), question]
    # overenie čísel beží nad textom dokumentu, nie nad krátkou správou v chate
    turn.verify_text = "\n".join(bodies)


async def _filter(turn: _Turn) -> AsyncIterator[Event]:
    # plne deterministická cesta — filter aj odpoveď robí kód, LLM sa nevolá
    plan = turn.plan
    matched, nearest = await _traced("rag.filter", turn.ctx, run_filter, plan)
    context, turn.sources = await run_in_threadpool(build_filter_context, plan, matched, nearest)
    yield _sources_event(turn.sources)
    answer_text = await run_in_threadpool(
        format_filter_answer, plan, matched, nearest, turn.sources
    )
    turn.answer_parts.append(answer_text)
    yield {"type": "delta", "text": answer_text}
    turn.evidence = [context, turn.question]


async def _compose(turn: _Turn) -> AsyncIterator[Event]:
    """Exact selection of a combination of items under a budget; agent loop as fallback."""
    question, ctx = turn.question, turn.ctx
    yield {"type": "step", "tool": "plan", "summary": "extrahujem plán výberu…"}
    categories = await run_in_threadpool(known_categories)
    composed = await _traced("rag.compose", ctx, run_compose, question, categories)
    if composed is not None:
        answer_text, turn.sources, turn.evidence = composed
        yield {"type": "step", "tool": "pick_items", "summary": "exaktný výber hotový"}
        yield _sources_event(turn.sources)
        turn.answer_parts.append(answer_text)
        yield {"type": "delta", "text": answer_text}
        return
    # plán sa nedal spoľahlivo vytiahnuť → voľná agent slučka
    yield {"type": "step", "tool": "agent", "summary": "plán neistý — agent slučka"}
    with span("rag.agent", parent=ctx):
        result = await run_agent(question)
    for step in result.trace:
        yield {"type": "step", "tool": step["tool"], "summary": step["args"]}
    turn.sources = result.sources
    yield _sources_event(turn.sources)
    turn.answer_parts.append(result.answer)
    yield {"type": "delta", "text": result.answer}
    turn.evidence = [*result.evidence, question]


async def _global(turn: _Turn) -> AsyncIterator[Event]:
    """Question about the whole library: concept communities and their summaries."""
    question, ctx = turn.question, turn.ctx
    yield {"type": "step", "tool": "communities", "summary": "hľadám témy naprieč knižnicou…"}
    communities = await _traced("global.communities", ctx, detect_communities)
    top = await _traced("global.rank", ctx, rank_communities, question, communities, 6)
    for theme in top:
        if not theme.summary:
            yield {
                "type": "step",
                "tool": "summarize",
                "summary": ", ".join(theme.concepts[:4]),
            }
            await _traced("global.summarize", ctx, summarize_community, theme)
    sources, messages = await _traced("global.context", ctx, global_context, question, top)
    turn.sources = sources
    yield {"type": "step", "tool": "themes", "summary": " · ".join(t.title or "" for t in top)}
    yield _sources_event(sources)
    stream = chat_stream(turn.settings.answer_model, messages, num_ctx=12288, parent=ctx)
    async for event in _stream_answer(turn, stream):
        yield event
    turn.evidence = [*(s.text for s in sources), *(t.summary or "" for t in top), question]


async def _research(turn: _Turn) -> AsyncIterator[Event]:
    """Deep research: subquestions → retrieval → findings → structured report."""
    question, ctx = turn.question, turn.ctx
    yield {"type": "step", "tool": "plan", "summary": "plánujem podotázky…"}
    subquestions = await _traced("research.plan", ctx, decompose, question, research=True)
    yield {"type": "step", "tool": "plan", "summary": " · ".join(subquestions)}
    per_query = await _traced("rag.retrieve", ctx, retrieve_for, subquestions, 6)
    sources = merge_sources(per_query)
    turn.sources = sources
    yield _sources_event(sources)
    findings: list[tuple[str, str]] = []
    for i, sub in enumerate(subquestions, start=1):
        yield subquestion_event(i, len(subquestions), sub)
        finding = await _traced(
            "research.finding", ctx, answer_subquestion, sub, sources, rag__subquestion=sub
        )
        findings.append((sub, finding))
    yield {"type": "step", "tool": "synthesis", "summary": "píšem správu…"}
    stream = chat_stream(
        turn.settings.answer_model,
        synthesis_messages(question, findings, sources),
        num_ctx=12288,
        parent=ctx,
    )
    async for event in _stream_answer(turn, stream):
        yield event
    turn.evidence = [*(s.text for s in sources), *(f for _, f in findings), question]


async def _lookup(turn: _Turn) -> AsyncIterator[Event]:
    """Classic RAG: retrieve → generate; also the fallback for every other mode."""
    question, ctx, settings = turn.question, turn.ctx, turn.settings
    if settings.scope_guard and not await _traced("rag.scope", ctx, _in_scope, question):
        async for event in _fixed(
            "refuse",
            reply_for(ScopeDecision("offtopic"), question),
            [{"tool": "guard", "summary": "mimo účelu"}],
        ):
            yield event
        turn.finished = True
        return
    if is_compound(question):
        # „Kto je stavbyvedúci a aká je pokuta?“ — jedno vyhľadávanie nájde len polovicu
        subquestions = await _traced("rag.decompose", ctx, decompose, question)
    else:
        subquestions = [question]
    if len(subquestions) > 1:
        yield {"type": "step", "tool": "decompose", "summary": " · ".join(subquestions)}
        per_query = await _traced("rag.retrieve", ctx, retrieve_for, subquestions, 5)
        sources = merge_sources(per_query, cap=10)
    else:
        sources = await _traced("rag.retrieve", ctx, retrieve_sources, question)
    turn.sources = sources
    yield _sources_event(sources)
    messages = build_messages(question, sources)
    if flagged := [s.n for s in sources if s.flagged]:
        yield {
            "type": "step",
            "tool": "guard",
            "summary": f"zdroje {flagged} obsahujú pokyny pre AI — označené ako dáta",
        }
    stream = chat_stream(settings.answer_model, messages, parent=ctx)
    async for event in _stream_answer(turn, stream):
        yield event
    turn.evidence = [*(s.text for s in sources), question]


_MODE_HANDLERS: dict[str, ModeHandler] = {
    "generate": _generate,
    "filter": _filter,
    "compose": _compose,
    "global": _global,
    "research": _research,
    "lookup": _lookup,
}


def _handler_for(plan: QueryPlan, settings: Settings) -> ModeHandler:
    # generate without a document type and compose without agent mode answer as lookup
    if plan.mode == "generate" and plan.doc_type is None:
        return _lookup
    if plan.mode == "compose" and not settings.agent_mode:
        return _lookup
    return _MODE_HANDLERS.get(plan.mode, _lookup)


async def _run(
    question: str, chat_id: str | None, ctx: Context, mode: str | None
) -> AsyncIterator[Event]:
    settings = get_settings()
    if settings.scope_guard:
        decision = check_question(question)
        if decision.blocked:
            async for event in _fixed(
                "refuse",
                reply_for(decision, question),
                [{"tool": "guard", "summary": decision.verdict}],
            ):
                yield event
            return
    if mode != "research":
        firma = await _traced("rag.firma", ctx, lambda: answer_firma(question, viewer_from_scope()))
        if firma is not None:
            async for event in _fixed("firma", firma.answer, firma.steps):
                yield event
            return

    if mode == "research":  # vynútené z UI (prepínač Deep research)
        plan = QueryPlan(mode="research")
    else:
        plan = await _traced("rag.route", ctx, route_query, question)
    yield {"type": "plan", "mode": plan.mode}

    turn = _Turn(question=question, plan=plan, chat_id=chat_id, ctx=ctx, settings=settings)
    async for event in _handler_for(plan, settings)(turn):
        yield event
    if turn.finished:
        return

    answer = redact_secrets("".join(turn.answer_parts))
    if settings.injection_guard:
        answer = sanitize_answer(answer)
    if plan.mode == "lookup" and is_refusal_answer(answer):
        # „podklady to neuvádzajú [1]“ — malý model citáciu pridá aj k odmietnutiu
        answer = CITE_RE.sub("", answer).strip()
        if settings.scope_guard and len(split_sentences(answer)) <= 1:
            # samotné odmietnutie → pevná veta, nič dopísané z vlastných vedomostí modelu
            answer = not_found_reply(question)
    verify_text = turn.verify_text if turn.verify_text is not None else answer
    issues = check_numbers(verify_text, turn.evidence)
    sentences: list[dict[str, Any]] = []
    if plan.mode in ("lookup", "research") and answer.strip():
        # deterministické cesty (filter, compose) píše kód zo zdrojov a global cituje témy,
        # nie vety — tam sa overujú len čísla
        checks = await _traced("rag.verify", ctx, verify_sentences, answer, turn.sources)
        sentences = [c.to_json() for c in checks]
    yield {"type": "verify", "ok": not issues, "issues": issues, "sentences": sentences}
    yield {
        "type": "final",
        "mode": plan.mode,
        "answer": answer,
        "sources": turn.sources,
        "document": turn.document,
        "trace_id": None,
        "verification": {"issues": issues, "sentences": sentences},
    }


@dataclass
class AnswerResult:
    mode: str
    answer: str
    sources: list[Source]
    document: dict[str, Any] | None = None
    steps: list[dict[str, str]] = field(default_factory=list)
    verify_issues: list[str] = field(default_factory=list)
    sentences: list[dict[str, Any]] = field(default_factory=list)
    trace_id: str | None = None


async def answer_question(question: str, *, mode: str | None = None) -> AnswerResult:
    """Nestreamovaná odpoveď — eval, MCP, API klienti."""
    steps: list[dict[str, str]] = []
    issues: list[str] = []
    async for event in run_query(question, mode=mode):
        if event["type"] == "step":
            steps.append({"tool": event["tool"], "summary": event["summary"]})
        elif event["type"] == "verify":
            issues = event["issues"]
        elif event["type"] == "final":
            return AnswerResult(
                mode=event["mode"],
                answer=event["answer"],
                sources=event["sources"],
                document=event["document"],
                steps=steps,
                verify_issues=issues,
                sentences=event["verification"]["sentences"],
                trace_id=event["trace_id"],
            )
    raise RuntimeError("run_query skončil bez final udalosti")

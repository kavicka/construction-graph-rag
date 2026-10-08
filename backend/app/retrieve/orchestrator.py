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
from app.company import load_company
from app.config import get_settings
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
from app.retrieve.router import QueryPlan, route_query
from app.retrieve.selfcheck import check_numbers
from app.retrieve.verify import is_refusal_answer, verify_sentences

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
        if history and needs_rewrite(question, history):
            standalone = await _traced(
                "rag.rewrite", ctx, rewrite_question, question, history,
                rag__history=len(history),
            )
            if standalone != question:
                set_attrs(root, rag__standalone_question=standalone)
                yield {"type": "rewrite", "question": standalone}
                question = standalone
        async for event in _run(question, chat_id, ctx, mode):
            if event["type"] == "final":
                event["trace_id"] = trace_id_of(root)
                set_attrs(root, rag__mode=event["mode"], rag__answer=event["answer"],
                          rag__sources=len(event["sources"]))
            elif event["type"] == "verify":
                weak = [x for x in event["sentences"] if x["status"] != "supported"]
                set_attrs(root, rag__verify_ok=event["ok"],
                          rag__sentences=len(event["sentences"]), rag__weak_sentences=len(weak))
            yield event
    except Exception as exc:
        root.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
        root.record_exception(exc)
        raise
    finally:
        root.end()


async def _run(
    question: str, chat_id: str | None, ctx: Context, mode: str | None
) -> AsyncIterator[Event]:
    settings = get_settings()
    if mode == "research":  # vynútené z UI (prepínač Deep research)
        plan = QueryPlan(mode="research")
    else:
        plan = await _traced("rag.route", ctx, route_query, question)
    yield {"type": "plan", "mode": plan.mode}

    sources: list[Source] = []
    evidence: list[str]
    answer_parts: list[str] = []
    document: dict[str, Any] | None = None
    verify_text: str | None = None  # None → overuje sa samotná odpoveď

    if plan.mode == "generate" and plan.doc_type is not None:
        doc_type, lang = plan.doc_type, plan.lang
        yield {"type": "step", "tool": "retrieve", "summary": "hľadám podklady…"}
        sources = await _traced("rag.retrieve", ctx, retrieve_sources, question, 10)
        yield _sources_event(sources)
        outline = await _traced(
            "docgen.outline", ctx, plan_outline, question, doc_type, lang, sources
        )
        yield {"type": "step", "tool": "outline", "summary": outline.title}
        company = await run_in_threadpool(load_company)
        bodies: list[str] = []
        for i, heading in enumerate(outline.headings, start=1):
            yield {"type": "step", "tool": "section",
                   "summary": f"{i}/{len(outline.headings)} {heading}"}
            bodies.append(
                await _traced(
                    "docgen.section", ctx, write_section, question, heading, outline, doc_type,
                    lang, sources, company, rag__heading=heading,
                )
            )
        yield {"type": "step", "tool": "render", "summary": "renderujem PDF…"}
        content = build_content(outline, doc_type, lang, bodies)
        generated = await _traced("docgen.render", ctx, save_document, content, company, sources,
                                  chat_id)
        document = generated.to_json()
        yield {"type": "document", "document": document}

        cited = {int(n) for b in bodies for m in CITE_RE.finditer(b) for n in m.group(1).split(",")}
        answer_text = summary_message(generated, lang, len(cited & {s.n for s in sources}))
        answer_parts.append(answer_text)
        yield {"type": "delta", "text": answer_text}
        evidence = [*(s.text for s in sources), question]
        # overenie čísel beží nad textom dokumentu, nie nad krátkou správou v chate
        verify_text = "\n".join(bodies)

    elif plan.mode == "filter":
        # plne deterministická cesta — filter aj odpoveď robí kód, LLM sa nevolá
        matched, nearest = await _traced("rag.filter", ctx, run_filter, plan)
        context, sources = build_filter_context(plan, matched, nearest)
        yield _sources_event(sources)
        answer_text = format_filter_answer(plan, matched, nearest, sources)
        answer_parts.append(answer_text)
        yield {"type": "delta", "text": answer_text}
        evidence = [context, question]

    elif plan.mode == "compose" and settings.agent_mode:
        yield {"type": "step", "tool": "plan", "summary": "extrahujem plán výberu…"}
        categories = sorted({r["category"] for r in db.list_records() if r["category"]})
        composed = await _traced("rag.compose", ctx, run_compose, question, categories)
        if composed is not None:
            answer_text, sources, evidence = composed
            yield {"type": "step", "tool": "pick_items", "summary": "exaktný výber hotový"}
            yield _sources_event(sources)
            answer_parts.append(answer_text)
            yield {"type": "delta", "text": answer_text}
        else:
            # plán sa nedal spoľahlivo vytiahnuť → voľná agent slučka
            yield {"type": "step", "tool": "agent", "summary": "plán neistý — agent slučka"}
            with span("rag.agent", parent=ctx):
                result = await run_agent(question)
            for step in result.trace:
                yield {"type": "step", "tool": step["tool"], "summary": step["args"]}
            sources = result.sources
            yield _sources_event(sources)
            answer_parts.append(result.answer)
            yield {"type": "delta", "text": result.answer}
            evidence = [*result.evidence, question]

    elif plan.mode == "global":
        yield {"type": "step", "tool": "communities", "summary": "hľadám témy naprieč knižnicou…"}
        communities = await _traced("global.communities", ctx, detect_communities)
        top = await _traced("global.rank", ctx, rank_communities, question, communities, 6)
        for theme in top:
            if not theme.summary:
                yield {"type": "step", "tool": "summarize",
                       "summary": ", ".join(theme.concepts[:4])}
                await _traced("global.summarize", ctx, summarize_community, theme)
        sources, messages = await _traced("global.context", ctx, global_context, question, top)
        yield {"type": "step", "tool": "themes",
               "summary": " · ".join(t.title or "" for t in top)}
        yield _sources_event(sources)
        stream = chat_stream(settings.answer_model, messages, num_ctx=12288, parent=ctx)
        async for token in strip_think(stream):
            answer_parts.append(token)
            yield {"type": "delta", "text": token}
        evidence = [*(s.text for s in sources), *(t.summary or "" for t in top), question]

    elif plan.mode == "research":
        yield {"type": "step", "tool": "plan", "summary": "plánujem podotázky…"}
        subquestions = await _traced("research.plan", ctx, decompose, question, research=True)
        yield {"type": "step", "tool": "plan", "summary": " · ".join(subquestions)}
        per_query = await _traced("rag.retrieve", ctx, retrieve_for, subquestions, 6)
        sources = merge_sources(per_query)
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
            settings.answer_model, synthesis_messages(question, findings, sources),
            num_ctx=12288, parent=ctx,
        )
        async for token in strip_think(stream):
            answer_parts.append(token)
            yield {"type": "delta", "text": token}
        evidence = [*(s.text for s in sources), *(f for _, f in findings), question]

    else:
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
        yield _sources_event(sources)
        messages = build_messages(question, sources)
        if flagged := [s.n for s in sources if s.flagged]:
            yield {"type": "step", "tool": "guard",
                   "summary": f"zdroje {flagged} obsahujú pokyny pre AI — označené ako dáta"}
        stream = chat_stream(settings.answer_model, messages, parent=ctx)
        async for token in strip_think(stream):
            answer_parts.append(token)
            yield {"type": "delta", "text": token}
        evidence = [*(s.text for s in sources), question]

    answer = "".join(answer_parts)
    if settings.injection_guard:
        answer = sanitize_answer(answer)
    if plan.mode == "lookup" and is_refusal_answer(answer):
        # „podklady to neuvádzajú [1]“ — malý model citáciu pridá aj k odmietnutiu
        answer = CITE_RE.sub("", answer).strip()
    issues = check_numbers(verify_text if verify_text is not None else answer, evidence)
    sentences: list[dict[str, Any]] = []
    if plan.mode in ("lookup", "research") and answer.strip():
        # deterministické cesty (filter, compose) píše kód zo zdrojov a global cituje témy,
        # nie vety — tam sa overujú len čísla
        checks = await _traced("rag.verify", ctx, verify_sentences, answer, sources)
        sentences = [c.to_json() for c in checks]
    yield {"type": "verify", "ok": not issues, "issues": issues, "sentences": sentences}
    yield {"type": "final", "mode": plan.mode, "answer": answer, "sources": sources,
           "document": document, "trace_id": None,
           "verification": {"issues": issues, "sentences": sentences}}


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
                mode=event["mode"], answer=event["answer"], sources=event["sources"],
                document=event["document"], steps=steps, verify_issues=issues,
                sentences=event["verification"]["sentences"], trace_id=event["trace_id"],
            )
    raise RuntimeError("run_query skončil bez final udalosti")

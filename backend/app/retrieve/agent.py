"""Agent slučka pre kompozičné otázky — LLM plánuje, nástroje počítajú.

Model dostane nástroje (Ollama tool-calling); každé číslo v odpovedi pochádza
z výsledku nástroja. Max AGENT_MAX_STEPS kôl — ochrana pred zacyklením.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.llm.ollama_client import OllamaError
from app.retrieve.pipeline import Source, retrieve_sources
from app.retrieve.records_query import build_filter_context, hits_to_sources, run_filter
from app.retrieve.router import Constraint, QueryPlan
from app.retrieve.solver import Slot, pick_items

logger = logging.getLogger(__name__)

AGENT_MAX_STEPS = 6

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "query_records",
            "description": (
                "Presný filter položiek podľa číselných atribútov (kcal, b=bielkoviny, "
                "s=sacharidy, t=tuky). Vracia VŠETKY vyhovujúce položky — výsledok je exaktný."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "constraints": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "attr": {"type": "string", "enum": ["kcal", "b", "s", "t"]},
                                "op": {"type": "string", "enum": ["<=", ">=", "<", ">"]},
                                "value": {"type": "number"},
                            },
                            "required": ["attr", "op", "value"],
                        },
                    },
                    "categories": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["constraints"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pick_items",
            "description": (
                "Exaktne vyberie kombináciu položiek (jedna na kategóriu) so súčtom "
                "atribútu do limitu, voliteľne maximalizuje iný atribút. Použi na "
                "otázky typu 'vyber X a Y aby sa spolu zmestili do N'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "budget_attr": {"type": "string", "enum": ["kcal", "b", "s", "t"]},
                    "budget_max": {"type": "number"},
                    "maximize": {"type": "string", "enum": ["kcal", "b", "s", "t"]},
                    "per_category_max": {
                        "type": "object",
                        "description": "voliteľný limit atribútu per kategória",
                        "additionalProperties": {"type": "number"},
                    },
                },
                "required": ["categories", "budget_attr", "budget_max"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_docs",
            "description": "Fulltext+sémantické vyhľadávanie v dokumentoch (na ne-číselné fakty).",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

AGENT_SYSTEM = """Si asistent nad lokálnou knižnicou PDF. Máš nástroje, ktoré počítajú PRESNE.
Pravidlá:
- Číselné filtre a výbery kombinácií VŽDY rieš nástrojmi query_records / pick_items.
  NIKDY nepočítaj ani neporovnávaj čísla sám.
- Kategórie položiek zisti z výsledkov nástrojov (pole category), neodhaduj ich.
- Odpovedz v jazyku otázky, stručne. Čísla uvádzaj presne z výsledkov nástrojov.
- Ak nástroje nič nenájdu, povedz to na rovinu."""


@dataclass
class AgentResult:
    answer: str
    sources: list[Source]
    trace: list[dict[str, str]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)  # výstupy nástrojov pre self-check


def _renumber(sources: list[Source]) -> list[Source]:
    seen: dict[str, Source] = {}
    for s in sources:
        seen.setdefault(s.chunk_id, s)
    out = list(seen.values())
    for i, s in enumerate(out, start=1):
        s.n = i
    return out


class ToolRuntime:
    """Vykonáva nástroje a zbiera zdroje na citovanie."""

    def __init__(self) -> None:
        self.sources: list[Source] = []
        self.evidence: list[str] = []

    def query_records(self, args: dict[str, Any]) -> str:
        constraints = [
            Constraint(str(c["attr"]), str(c["op"]), float(c["value"]))
            for c in args.get("constraints", [])
            if c.get("attr") and c.get("op")
        ]
        plan = QueryPlan(
            mode="filter",
            constraints=constraints,
            categories=[str(c) for c in args.get("categories", [])],
        )
        matched, nearest = run_filter(plan)
        context, sources = build_filter_context(plan, matched, nearest)
        self.sources += sources
        return context

    def pick_items(self, args: dict[str, Any]) -> str:
        per_cat_max = {
            str(k): float(v) for k, v in (args.get("per_category_max") or {}).items()
        }
        budget_attr = str(args["budget_attr"])
        slots = [
            Slot(
                category=str(cat),
                constraints=(
                    [Constraint(budget_attr, "<=", per_cat_max[str(cat)])]
                    if str(cat) in per_cat_max
                    else []
                ),
            )
            for cat in args["categories"]
        ]
        combo = pick_items(
            slots,
            budget_attr=budget_attr,
            budget_max=float(args["budget_max"]),
            maximize=str(args["maximize"]) if args.get("maximize") else None,
        )
        if combo is None:
            return "V zadaných kategóriách nie je z čoho vyberať."
        hits = [h for _, h in combo.picks]
        self.sources += hits_to_sources(hits)
        lines = [
            "EXAKTNÝ výber kombinácie (spočítané kódom):"
            if combo.within_budget
            else "Limit sa NEDÁ splniť; najbližšia možná kombinácia (spočítané kódom):"
        ]
        for cat, h in combo.picks:
            attrs = ", ".join(f"{k}={v:g}" for k, v in sorted(h.attrs.items()) if len(k) <= 4)
            lines.append(f"- {cat}: {h.name} ({attrs})")
        totals = ", ".join(f"{k}={v:g}" for k, v in combo.total.items())
        lines.append(f"Súčet: {totals}")
        return "\n".join(lines)

    def search_docs(self, args: dict[str, Any]) -> str:
        sources = retrieve_sources(str(args.get("query", "")), k=5)
        self.sources += sources
        return "\n\n".join(
            f"[{s.n}] ({s.filename}, str. {s.page_from}): {s.text[:600]}" for s in sources
        )

    def execute(self, name: str, args: dict[str, Any]) -> str:
        handler = {
            "query_records": self.query_records,
            "pick_items": self.pick_items,
            "search_docs": self.search_docs,
        }.get(name)
        if handler is None:
            return f"Neznámy nástroj: {name}"
        try:
            result = handler(args)
        except Exception as exc:
            logger.exception("nástroj %s zlyhal", name)
            return f"Nástroj {name} zlyhal: {exc}"
        self.evidence.append(result)
        return result


async def _chat_with_tools(messages: list[dict[str, Any]]) -> dict[str, Any]:
    settings = get_settings()
    payload = {
        "model": settings.answer_model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=5.0)) as client:
            resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
    except httpx.HTTPError as exc:
        raise OllamaError(f"agent chat zlyhal: {exc}") from exc
    if err := data.get("error"):
        raise OllamaError(f"agent chat: {err}")
    message: dict[str, Any] = data.get("message", {})
    return message


async def run_agent(question: str) -> AgentResult:
    runtime = ToolRuntime()
    trace: list[dict[str, str]] = []
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": question},
    ]

    for _step in range(AGENT_MAX_STEPS):
        message = await _chat_with_tools(messages)
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            answer = (message.get("content") or "").strip()
            return AgentResult(
                answer=answer or "Nepodarilo sa zostaviť odpoveď.",
                sources=_renumber(runtime.sources),
                trace=trace,
                evidence=runtime.evidence,
            )
        messages.append(message)
        for call in tool_calls:
            fn = call.get("function", {})
            name = str(fn.get("name", ""))
            raw_args = fn.get("arguments") or {}
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
            result = runtime.execute(name, args)
            trace.append({"tool": name, "args": json.dumps(args, ensure_ascii=False)[:200]})
            messages.append({"role": "tool", "tool_name": name, "content": result})

    return AgentResult(
        answer="Prekročený limit krokov agenta — skús otázku rozdeliť.",
        sources=_renumber(runtime.sources),
        trace=trace,
        evidence=runtime.evidence,
    )

"""Query router — decides which path a question takes.

lookup  → classic RAG (retrieve → generate)
filter  → SQL over records: numeric conditions are computed by code, not the LLM
compose → agent loop (choosing a combination of items within a budget)
generate → branded PDF document from the found sources (app.docgen)
research → deep research: sub-questions → retrieval → findings → structured report
global   → question about the whole library: graph concept communities + summaries (LazyGraphRAG)

Heuristics first (deterministic, free), LLM fallback only when the question contains
a number but the heuristics cannot determine the condition.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from rapidfuzz import fuzz

from app import db
from app.auth import allowed_doc_ids
from app.config import get_settings
from app.docgen.intent import detect_generate_intent
from app.docgen.schema import DocType, Lang
from app.graph.communities import is_global_query
from app.graph.schema import CONCEPT
from app.llm.ollama_client import OllamaError, generate_sync
from app.retrieve.research import is_research_request

logger = logging.getLogger(__name__)

Mode = Literal["lookup", "filter", "compose", "generate", "research", "global"]


@dataclass
class Constraint:
    attr: str  # kcal | b | s | t | val_g | …
    op: str  # <= | >= | < | >
    value: float


@dataclass
class QueryPlan:
    mode: Mode
    constraints: list[Constraint] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    maximize: str | None = None  # attribute to maximise (e.g. most protein)
    doc_ids: list[str] = field(
        default_factory=list
    )  # filter restricted to the documents from the question
    doc_type: DocType | None = None  # only for mode="generate"
    lang: Lang = "sk"


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


LE_RE = (
    r"(?:menej ako|mene nez|nizs\w* ako|mensi\w* ako|lacnejs\w* ako|less than|lower than|"
    r"cheaper than|maximalne|najviac|nejvyse|under|below|max|do|pod)"
)
GE_RE = (
    r"(?:viac ako|vice nez|vyss\w* ako|vacs\w* ako|drahs\w* ako|more than|higher than|"
    r"greater than|at least|minimalne|alespon|aspon|over|above|min|nad)"
)
# „400 000" (thousands separator) and „36,4"
NUM = r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"

ATTR_HINTS: dict[str, tuple[str, ...]] = {
    "kcal": ("kcal", "kalor", "calor", "energ"),
    "b": ("bielkov", "bilkov", "protein"),
    "s": ("sacharid", "carb"),
    "t": ("tuk", "fat"),
}
COMPOSE_HINTS = (
    "spolu",
    "dokopy",
    "celkovo",
    "zmestil",
    "zmestim",
    "total",
    "kombin",
    "vyber mi aj",
    "potom mi vyber",
    "este",
)
MAXIMIZE_HINTS: dict[str, tuple[str, ...]] = {
    "b": ("najviac bielkov", "nejvic bilkov", "most protein"),
    "kcal": ("najviac kalo", "najvyssi prijem"),
}


def _value(token: str) -> float:
    return float(re.sub(r"[ \u00a0]", "", token).replace(",", "."))


def _attr_vocab() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """attribute → (label stems, units). Fixed macros + registry from the library's tables."""
    vocab: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        k: (hints, ()) for k, hints in ATTR_HINTS.items()
    }
    for a in db.list_record_attrs():
        if a["key"] in vocab:
            continue
        stems = tuple(
            tok[: max(4, len(tok) - 2)] for tok in _norm(a["label"]).split() if len(tok) >= 4
        )
        units = (_norm(a["unit"]),) if a["unit"] else ()
        vocab[a["key"]] = (stems, units)
    return vocab


def _attr_near(
    text: str, start: int, end: int, vocab: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]
) -> str | None:
    """Attribute of a condition: unit right after the number (3), label after the number (2),
    before it (1).
    „do 820 kalórií a nad 50 g bielkovín": for 50, protein after the number beats
    calories before it."""
    after = text[end : end + 30]
    before = text[max(0, start - 45) : start]
    best: tuple[int, str] | None = None
    for attr, (stems, units) in vocab.items():
        score = 0
        if any(re.match(rf"\s*{re.escape(u)}(?![a-z])", after) for u in units):
            score += 3
        # stem only at the start of a word — „hodno“ (Hodnotenie) must not hit „vyhodnotenia“
        if any(re.search(rf"\b{re.escape(h)}", after) for h in stems):
            score += 2
        elif any(re.search(rf"\b{re.escape(h)}", before) for h in stems):
            score += 1
        if score and (best is None or score > best[0]):
            best = (score, attr)
    return best[1] if best else None


def _heuristic_constraints(qn: str) -> list[Constraint]:
    vocab = _attr_vocab()
    constraints: list[Constraint] = []
    for pattern, op in ((LE_RE, "<="), (GE_RE, ">=")):
        for m in re.finditer(rf"\b{pattern}\s+{NUM}", qn):
            attr = _attr_near(qn, m.start(), m.end(), vocab)
            if attr is None and op == "<=" and "kcal" in qn:
                attr = "kcal"
            if attr:
                constraints.append(Constraint(attr, op, _value(m.group(1))))
    return constraints


def _match_documents(qn: str) -> list[str]:
    """Documents whose file name the question mentions („v rozpočte Agátová") — the filter is
    restricted to them. Match on a 5-character stem because of Slovak inflection
    (Agátovej ↔ agatova)."""
    q_stems = {t[:5] for t in re.findall(r"[a-z0-9]{4,}", qn)}
    scored: list[tuple[int, str]] = []
    for d in db.list_documents():
        tokens = re.findall(r"[a-z0-9]{4,}", _norm(d["filename"].rsplit(".", 1)[0]))
        score = sum(1 for t in set(tokens) if t[:5] in q_stems)
        if score:
            scored.append((score, d["id"]))
    if not scored:
        return []
    top = max(sc for sc, _ in scored)
    return [doc_id for sc, doc_id in scored if sc == top]


def known_categories() -> list[str]:
    """Record categories of the documents the current access scope may see. They end up
    in LLM prompts, so categories of a document hidden from the user must not."""
    return db.list_record_categories(allowed_doc_ids())


def _match_categories(qn: str) -> list[str]:
    """Categories from the DB mentioned in the question — data-driven, no hardcoded words."""
    matched = []
    for cat in known_categories():
        cat_n = _norm(cat)
        tokens = [t for t in cat_n.split() if len(t) >= 4]
        if any(fuzz.partial_ratio(t, qn) >= 85 for t in tokens):
            matched.append(cat)
    return matched


def mentions_library(question: str) -> bool:
    """Does the question mention anything from the library — a document, record category,
    graph concept?
    Then it is definitely in scope and the LLM scope classifier is not called."""
    qn = _norm(question)
    if _match_documents(qn) or _match_categories(qn):
        return True
    for node in db.list_graph_nodes(types=[CONCEPT]):
        label = _norm(node["label"])
        # short labels („C30“, „NP“) would be found in anything
        if len(label) >= 5 and fuzz.partial_ratio(label, qn) >= 90:
            return True
    return False


class _LlmPlan(BaseModel):
    mode: Literal[
        "lookup", "filter", "compose"
    ]  # generate/research are determined deterministically
    constraints: list[dict[str, str | float]] = Field(default_factory=list, max_length=4)


def _llm_fallback(question: str) -> QueryPlan | None:
    settings = get_settings()
    prompt = (
        "Klasifikuj otázku nad databázou položiek s číselnými atribútmi "
        "(kcal, b=bielkoviny, s=sacharidy, t=tuky).\n"
        'mode: "filter" = hľadá položky podľa číselnej podmienky; '
        '"compose" = skladá viac položiek do súčtového limitu; inak "lookup".\n'
        'constraints: [{"attr": "kcal|b|s|t", "op": "<=|>=", "value": číslo}]\n'
        f"Otázka: {question}"
    )
    try:
        raw = generate_sync(
            settings.extract_model,
            prompt,
            json_schema=_LlmPlan.model_json_schema(),
            num_ctx=2048,
        )
        parsed = _LlmPlan.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("router LLM fallback zlyhal: %s", exc)
        return None
    constraints = []
    for c in parsed.constraints:
        try:
            attr, op, value = str(c["attr"]), str(c["op"]), float(c["value"])
            if attr in ATTR_HINTS and op in ("<=", ">=", "<", ">"):  # LLM fallback = macros
                constraints.append(Constraint(attr, op, value))
        except (KeyError, TypeError, ValueError):
            continue
    if parsed.mode == "lookup" or not constraints:
        return None
    return QueryPlan(mode=parsed.mode, constraints=constraints)


def route_query(question: str) -> QueryPlan:
    # a document command takes precedence — „sprav ponuku do 500 kcal“ is still a document
    if intent := detect_generate_intent(question):
        return QueryPlan(mode="generate", doc_type=intent.doc_type, lang=intent.lang)
    if is_research_request(question):
        return QueryPlan(mode="research")
    if is_global_query(question):
        return QueryPlan(mode="global")

    qn = _norm(question)
    constraints = _heuristic_constraints(qn)
    categories = _match_categories(qn)
    maximize = next(
        (attr for attr, hints in MAXIMIZE_HINTS.items() if any(h in qn for h in hints)), None
    )

    if not constraints and not maximize:
        # a number in the question but the heuristics found no condition → try the LLM
        if re.search(rf"{NUM}\s*(kcal|g\b)", qn):
            llm_plan = _llm_fallback(question)
            if llm_plan is not None:
                llm_plan.categories = categories
                return llm_plan
        return QueryPlan(mode="lookup", categories=categories)

    compose = (
        len(categories) >= 2
        or any(h in qn for h in COMPOSE_HINTS)
        or len({c.attr for c in constraints if c.op == "<="}) > 1
        and len(categories) >= 1
    )
    return QueryPlan(
        mode="compose" if compose else "filter",
        constraints=constraints,
        categories=categories,
        maximize=maximize,
        doc_ids=[] if categories else _match_documents(qn),
    )

"""Query router — rozhodne, ktorou cestou otázka pôjde.

lookup  → klasický RAG (retrieve → generate)
filter  → SQL nad records: číselné podmienky počíta kód, nie LLM
compose → agent slučka (výber kombinácie položiek do rozpočtu)

Heuristiky first (deterministické, zadarmo), LLM fallback len keď je v otázke
číslo, ale heuristika nevie určiť podmienku.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from rapidfuzz import fuzz

from app import db
from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync

logger = logging.getLogger(__name__)

Mode = Literal["lookup", "filter", "compose"]


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
    maximize: str | None = None  # atribút na maximalizáciu (napr. najviac bielkovín)


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


LE_RE = r"(?:do|pod|max|maximalne|najviac|nejvyse|under|below|less than|menej ako|mene nez)"
GE_RE = r"(?:nad|aspon|alespon|min|minimalne|over|above|at least|viac ako|vice nez)"
NUM = r"(\d+(?:[.,]\d+)?)"

ATTR_HINTS: dict[str, tuple[str, ...]] = {
    "kcal": ("kcal", "kalor", "calor", "energ"),
    "b": ("bielkov", "bilkov", "protein"),
    "s": ("sacharid", "carb"),
    "t": ("tuk", "fat"),
}
COMPOSE_HINTS = (
    "spolu", "dokopy", "celkovo", "zmestil", "zmestim", "total", "kombin",
    "vyber mi aj", "potom mi vyber", "este",
)
MAXIMIZE_HINTS: dict[str, tuple[str, ...]] = {
    "b": ("najviac bielkov", "nejvic bilkov", "most protein"),
    "kcal": ("najviac kalo", "najvyssi prijem"),
}


def _attr_near(text: str, pos: int, window: int = 40) -> str | None:
    ctx = text[pos : pos + window]
    for attr, hints in ATTR_HINTS.items():
        if any(h in ctx for h in hints):
            return attr
    return None


def _heuristic_constraints(qn: str) -> list[Constraint]:
    constraints: list[Constraint] = []
    for m in re.finditer(rf"{LE_RE}\s+{NUM}", qn):
        attr = _attr_near(qn, m.end()) or ("kcal" if "kcal" in qn else None)
        if attr:
            constraints.append(Constraint(attr, "<=", float(m.group(1).replace(",", "."))))
    for m in re.finditer(rf"{GE_RE}\s+{NUM}", qn):
        attr = _attr_near(qn, m.end())
        if attr:
            constraints.append(Constraint(attr, ">=", float(m.group(1).replace(",", "."))))
    return constraints


def _known_categories() -> list[str]:
    return sorted({r["category"] for r in db.list_records() if r["category"]})


def _match_categories(qn: str) -> list[str]:
    """Kategórie z DB spomenuté v otázke — data-driven, žiadne hardcoded slová."""
    matched = []
    for cat in _known_categories():
        cat_n = _norm(cat)
        tokens = [t for t in cat_n.split() if len(t) >= 4]
        if any(fuzz.partial_ratio(t, qn) >= 85 for t in tokens):
            matched.append(cat)
    return matched


class _LlmPlan(BaseModel):
    mode: Literal["lookup", "filter", "compose"]
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
            settings.extract_model, prompt,
            json_schema=_LlmPlan.model_json_schema(), num_ctx=2048,
        )
        parsed = _LlmPlan.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("router LLM fallback zlyhal: %s", exc)
        return None
    constraints = []
    for c in parsed.constraints:
        try:
            attr, op, value = str(c["attr"]), str(c["op"]), float(c["value"])
            if attr in ATTR_HINTS and op in ("<=", ">=", "<", ">"):
                constraints.append(Constraint(attr, op, value))
        except (KeyError, TypeError, ValueError):
            continue
    if parsed.mode == "lookup" or not constraints:
        return None
    return QueryPlan(mode=parsed.mode, constraints=constraints)


def route_query(question: str) -> QueryPlan:
    qn = _norm(question)
    constraints = _heuristic_constraints(qn)
    categories = _match_categories(qn)
    maximize = next(
        (attr for attr, hints in MAXIMIZE_HINTS.items() if any(h in qn for h in hints)), None
    )

    if not constraints and not maximize:
        # číslo v otázke, ale heuristika podmienku nenašla → skús LLM
        if re.search(rf"{NUM}\s*(kcal|g\b)", qn):
            llm_plan = _llm_fallback(question)
            if llm_plan is not None:
                llm_plan.categories = categories
                return llm_plan
        return QueryPlan(mode="lookup", categories=categories)

    compose = (
        len(categories) >= 2
        or any(h in qn for h in COMPOSE_HINTS)
        or len({c.attr for c in constraints if c.op == "<="}) > 1 and len(categories) >= 1
    )
    return QueryPlan(
        mode="compose" if compose else "filter",
        constraints=constraints,
        categories=categories,
        maximize=maximize,
    )

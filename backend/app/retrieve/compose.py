"""Composition questions: plan-then-execute instead of a free agent loop.

The LLM does ONE thing — assigns numbers from the question to categories (schema-forced JSON,
no arithmetic). Validation: every number in the plan MUST be in the question — if the model
computed something itself, the plan is discarded and we fall back to the agent loop. Picking the
combination and the answer are then purely deterministic (pick_items + template).
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from rapidfuzz import fuzz

from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync
from app.retrieve.pipeline import Source
from app.retrieve.records_query import _norm, hits_to_sources
from app.retrieve.router import Constraint
from app.retrieve.solver import Combo, Slot, pick_items

logger = logging.getLogger(__name__)

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")

ATTR_LABELS = {"kcal": "kcal", "b": "bielkoviny (g)", "s": "sacharidy (g)", "t": "tuky (g)"}


class ComposeSlot(BaseModel):
    category: str
    max_value: float | None = None  # limit of the budget attribute for this slot


class ComposePlan(BaseModel):
    slots: list[ComposeSlot] = Field(min_length=1, max_length=4)
    budget_attr: Literal["kcal", "b", "s", "t"] = "kcal"
    budget_max: float
    maximize: Literal["kcal", "b", "s", "t"] | None = None


PLAN_PROMPT = """Z otázky vytiahni plán výberu položiek. NIČ NEPOČÍTAJ — čísla iba
opíš presne tak, ako sú v otázke.

Dostupné kategórie (category použi PRESNE z tohto zoznamu):
{categories}

- slots: ktoré kategórie sa vyberajú; max_value = limit uvedený v otázke pre danú
  kategóriu (ak nie je, vynechaj)
- budget_max: celkový súčtový limit z otázky
- budget_attr: atribút limitu (kcal/b/s/t)
- maximize: čo sa má maximalizovať (napr. bielkoviny = b), ak otázka hovorí

Otázka: {question}"""


def _question_numbers(question: str) -> set[float]:
    return {float(n.replace(",", ".")) for n in NUM_RE.findall(question)}


# Cosine threshold for the embedding fallback by model (calibrated on SK/CZ meal categories:
# raňajky→Snídaně 0.84 yes, večeře→Snídaně 0.74 and the made-up one 0.39 no).
# E5 has an anisotropic space — every pair of words ends up at ~0.77–0.89 and raňajky are
# closer to obed than to snídaně, so no threshold separates them → fallback disabled (None).
CATEGORY_SIM_FLOOR: dict[str, float | None] = {
    "BAAI/bge-m3": 0.80,
    "intfloat/multilingual-e5-small": None,
}
DEFAULT_CATEGORY_SIM_FLOOR = 0.80


def _category_sim_floor(model_name: str) -> float | None:
    return CATEGORY_SIM_FLOOR.get(model_name, DEFAULT_CATEGORY_SIM_FLOOR)


def _match_known_category(name: str, known: list[str]) -> str | None:
    """Lexical (token_set) → embedding fallback — SK↔CZ synonyms
    (raňajky ↔ snídaně) are not solved by lexicon, a multilingual space solves them."""
    best, best_score = None, 0.0
    for cat in known:
        score = fuzz.token_set_ratio(_norm(name), _norm(cat))
        if score > best_score:
            best, best_score = cat, score
    if best_score >= 75:
        return best

    from app.index.embedder import get_embedder

    embedder = get_embedder()
    floor = _category_sim_floor(embedder.model_name)
    if floor is None:
        return None
    query_vec = embedder.embed_query(name)
    cat_vecs = embedder.embed_passages(known)

    def cosine(a: list[float], b: list[float]) -> float:
        num = sum(x * y for x, y in zip(a, b, strict=True))
        da = sum(x * x for x in a) ** 0.5
        db = sum(y * y for y in b) ** 0.5
        return num / (da * db) if da and db else 0.0

    scored = [(cosine(query_vec, v), c) for v, c in zip(cat_vecs, known, strict=True)]
    sim, cat = max(scored)
    return cat if sim >= floor else None


def extract_compose_plan(question: str, known_categories: list[str]) -> ComposePlan | None:
    settings = get_settings()
    prompt = PLAN_PROMPT.format(
        categories="\n".join(f"- {c}" for c in known_categories), question=question
    )
    try:
        raw = generate_sync(
            settings.answer_model,
            prompt,
            json_schema=ComposePlan.model_json_schema(),
            num_ctx=4096,
        )
        plan = ComposePlan.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("compose plán zlyhal: %s", exc)
        return None

    # hard validation: the model must not invent numbers
    allowed = _question_numbers(question)
    if plan.budget_max not in allowed:
        logger.warning("compose plán: budget %s nie je v otázke %s", plan.budget_max, allowed)
        return None
    # an invented per-slot limit (the model likes to compute differences) does not discard the whole
    # plan — the limit is just cleared; correctness is held by budget_max in pick_items
    for slot in plan.slots:
        if slot.max_value is not None and slot.max_value not in allowed:
            logger.warning("compose plán: slot limit %s mimo otázky — mažem", slot.max_value)
            slot.max_value = None

    # map categories to known ones (the model may return 'obed' → 'Oběd varianty')
    for slot in plan.slots:
        matched = _match_known_category(slot.category, known_categories)
        if matched is None:
            logger.warning("compose plán: neznáma kategória %r", slot.category)
            return None
        slot.category = matched
    if len({s.category for s in plan.slots}) < len(plan.slots):
        return None
    return plan


def _fmt_attrs(attrs: dict[str, float]) -> str:
    return ", ".join(f"{k}={v:g}" for k, v in sorted(attrs.items()) if len(k) <= 4)


def format_combo_answer(plan: ComposePlan, combo: Combo, sources: list[Source]) -> str:
    table_n = {s.table_id: s.n for s in sources}
    header = (
        "Presný výber (počítané kódom):"
        if combo.within_budget
        else f"Limit {plan.budget_max:g} {plan.budget_attr} sa NEDÁ splniť — "
        "najbližšia možná kombinácia:"
    )
    lines = [header, ""]
    for cat, hit in combo.picks:
        n = table_n.get(hit.table_id, 1)
        lines.append(f"- **{cat}**: {hit.name} ({_fmt_attrs(hit.attrs)}) [{n}]")
    totals = ", ".join(f"{ATTR_LABELS.get(k, k)} {v:g}" for k, v in combo.total.items())
    lines.append("")
    if combo.within_budget:
        lines.append(f"Súčet: {totals} — limit {plan.budget_max:g} {plan.budget_attr} dodržaný.")
    else:
        over = combo.total[plan.budget_attr] - plan.budget_max
        lines.append(
            f"Súčet: {totals} — o {over:g} {plan.budget_attr} nad limitom {plan.budget_max:g}."
        )
    return "\n".join(lines)


def run_compose(
    question: str, known_categories: list[str]
) -> tuple[str, list[Source], list[str]] | None:
    """→ (answer, sources, evidence) or None → fall back to the agent loop."""
    plan = extract_compose_plan(question, known_categories)
    if plan is None:
        return None

    slots = [
        Slot(
            category=s.category,
            constraints=([Constraint(plan.budget_attr, "<=", s.max_value)] if s.max_value else []),
        )
        for s in plan.slots
    ]
    combo = pick_items(
        slots,
        budget_attr=plan.budget_attr,
        budget_max=plan.budget_max,
        maximize=plan.maximize,
    )
    if combo is None:
        cats = ", ".join(s.category for s in plan.slots)
        answer = f"V kategóriách ({cats}) nie sú položky, z ktorých by sa dala zostaviť kombinácia."
        return answer, [], [question]

    hits = [h for _, h in combo.picks]
    sources = hits_to_sources(hits)
    answer = format_combo_answer(plan, combo, sources)
    return answer, sources, [answer, question]

"""Exact selection of an item combination within a budget (composition questions).

„Obed do 870 + raňajky, spolu do 1500 kcal, čo najviac bielkovín" (lunch up to 870 + breakfast,
1500 kcal total, as much protein as possible) is a small combinatorial problem —
solved exactly in code, not estimated by the LLM.
Prune: at most TOP_PER_SLOT candidates per slot by objective → the slot product stays small.
"""

import itertools
from dataclasses import dataclass, field

from app.retrieve.records_query import RecordHit, _passes, load_hits
from app.retrieve.router import Constraint

TOP_PER_SLOT = 12


@dataclass
class Slot:
    category: str
    constraints: list[Constraint] = field(default_factory=list)


@dataclass
class Combo:
    picks: list[tuple[str, RecordHit]]  # (category, item)
    total: dict[str, float]
    within_budget: bool = True


def _sum_attr(items: list[RecordHit], attr: str) -> float:
    return sum(h.attrs.get(attr, 0.0) for h in items)


def pick_items(
    slots: list[Slot],
    budget_attr: str,
    budget_max: float,
    maximize: str | None = None,
) -> Combo | None:
    """Best combination: one item per slot, sum of budget_attr ≤ budget_max,
    maximises `maximize` (default: use as much of the budget as possible)."""
    per_slot: list[list[RecordHit]] = []
    for slot in slots:
        candidates = [
            h
            for h in load_hits([slot.category])
            if budget_attr in h.attrs and _passes(h.attrs, slot.constraints)
        ]
        if not candidates:
            return None
        key = maximize or budget_attr
        candidates.sort(key=lambda h: h.attrs.get(key, 0.0), reverse=True)
        per_slot.append(candidates[:TOP_PER_SLOT])

    def _totals(items: list[RecordHit], total_budget: float) -> dict[str, float]:
        totals: dict[str, float] = {budget_attr: total_budget}
        if maximize:
            totals[maximize] = _sum_attr(items, maximize)
        return totals

    best: Combo | None = None
    best_score = float("-inf")
    nearest: Combo | None = None
    nearest_total = float("inf")
    for combo in itertools.product(*per_slot):
        items = list(combo)
        total_budget = _sum_attr(items, budget_attr)
        if total_budget > budget_max:
            # does not meet the limit — keep the smallest sum as the "closest option"
            if total_budget < nearest_total:
                nearest_total = total_budget
                nearest = Combo(
                    picks=[(slots[i].category, h) for i, h in enumerate(items)],
                    total=_totals(items, total_budget),
                    within_budget=False,
                )
            continue
        score = _sum_attr(items, maximize) if maximize else total_budget
        if score > best_score:
            best_score = score
            best = Combo(
                picks=[(slots[i].category, h) for i, h in enumerate(items)],
                total=_totals(items, total_budget),
            )
    return best or nearest

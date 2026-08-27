"""Exaktný výber kombinácie položiek do rozpočtu (kompozičné otázky).

„Obed do 870 + raňajky, spolu do 1500 kcal, čo najviac bielkovín" je malý
kombinatorický problém — rieši sa presne v kóde, nie odhadom v LLM.
Prune: na slot max TOP_PER_SLOT kandidátov podľa cieľa → súčin slotov ostáva malý.
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
    picks: list[tuple[str, RecordHit]]  # (kategória, položka)
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
    """Najlepšia kombinácia: jedna položka na slot, súčet budget_attr ≤ budget_max,
    maximalizuje `maximize` (default: čo najplnšie využitie rozpočtu)."""
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
            # limit nespĺňa — drž najmenší súčet ako „najbližšiu možnosť"
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

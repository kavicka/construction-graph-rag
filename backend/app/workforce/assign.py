"""Návrh pracovníka pre importovanú úlohu — počíta kód, nie LLM.

1. Meno v dokumente → ten pracovník (fuzzy, aj len priezvisko / krstné meno).
2. Inak skóre: zhoda profesie s typom práce (+3), už robí na tejto stavbe (+2),
   menšie vyťaženie v termíne (−1 za každú súbežnú otvorenú úlohu).
Bez zhody profesie aj stavby sa nenavrhuje nikto — úloha ostane Nepriradená.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from rapidfuzz import fuzz

from app.workforce.sk_numbers import normalize
from app.workforce.tasks import OPEN_STATUSES, overlaps

NAME_MATCH_MIN = 88
W_PROFESSION, W_ON_SITE, W_LOAD = 3.0, 2.0, 1.0


@dataclass
class Suggestion:
    user_id: str | None
    reason: str
    basis: str  # "named" | "scored" | "none"


def match_person(person: str | None, workers: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not person or not person.strip():
        return None
    target = normalize(person)
    tokens = [t for t in target.split() if len(t) >= 3]  # „p. Kováč“ → kovac
    scored: list[tuple[float, dict[str, Any]]] = []
    for w in workers:
        name = normalize(w["name"])
        parts = name.split()
        score = max(
            fuzz.token_set_ratio(target, name),
            max((fuzz.ratio(t, p) for t in tokens for p in parts), default=0),
        )
        scored.append((score, w))
    scored.sort(key=lambda s: -s[0])
    if not scored or scored[0][0] < NAME_MATCH_MIN:
        return None
    # dvaja „Jánovia“ — nejednoznačné, radšej nikoho
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def _plural_tasks(n: int) -> str:
    return "úloha" if n == 1 else "úlohy" if 2 <= n <= 4 else "úloh"


def suggest_worker(
    *,
    person: str | None,
    work_type: str | None,
    site_id: str,
    date_from: date | None,
    date_to: date | None,
    workers: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> Suggestion:
    active = [w for w in workers if w.get("active", 1)]
    if named := match_person(person, active):
        return Suggestion(named["id"], f"menovaný v dokumente ({person})", "named")

    start = date_from or date.today()
    end = date_to or start
    open_tasks = [t for t in tasks if t["status"] in OPEN_STATUSES]
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for w in active:
        professions = {normalize(p) for p in json.loads(w.get("professions_json") or "[]")}
        mine = [t for t in open_tasks if w["id"] in t["assignees"]]
        on_site = any(t["site_id"] == site_id for t in mine)
        load = sum(1 for t in mine if overlaps(t, start, end))
        has_prof = bool(work_type) and normalize(work_type or "") in professions
        if not (has_prof or on_site):
            continue
        score = W_PROFESSION * has_prof + W_ON_SITE * on_site - W_LOAD * load
        reasons = []
        if has_prof:
            reasons.append(work_type or "")
        if on_site:
            reasons.append("už na stavbe")
        reasons.append(f"{load} {_plural_tasks(load)} v termíne")
        scored.append((score, " · ".join(reasons), w))
    if not scored:
        what = f"profesiu „{work_type}“" if work_type else "vhodnú profesiu"
        return Suggestion(None, f"nikto nemá {what} ani nerobí na stavbe", "none")
    scored.sort(key=lambda s: (-s[0], s[2]["name"]))
    best = scored[0]
    return Suggestion(best[2]["id"], best[1], "scored")

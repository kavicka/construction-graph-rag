"""Presný filter nad records — čísla porovnáva kód, LLM len formuluje.

Vracia štruktúrovaný výsledok + zdroje (tabuľka → dokument + strana), aby
citácie fungovali rovnako ako pri klasickom RAG.
"""

import json
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from app import db
from app.retrieve.pipeline import Source
from app.retrieve.router import Constraint, QueryPlan


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


@dataclass
class RecordHit:
    name: str
    category: str | None
    attrs: dict[str, float]
    doc_id: str
    table_id: str
    page_no: int | None
    raw_text: str


def _category_matches(record_category: str | None, wanted: list[str]) -> bool:
    """Exact po normalizácii, inak token_set — partial_ratio matchoval generické
    slovo ('varianty') a prepúšťal cudzie kategórie (Oběd ↔ Večeře)."""
    if not wanted:
        return True
    if not record_category:
        return False
    cat = _norm(record_category)
    for w in wanted:
        wn = _norm(w)
        if wn == cat or fuzz.token_set_ratio(wn, cat) >= 90:
            return True
    return False


def _holds(value: float, op: str, target: float) -> bool:
    return {
        "<=": value <= target,
        ">=": value >= target,
        "<": value < target,
        ">": value > target,
    }[op]


def _passes(attrs: dict[str, float], constraints: list[Constraint]) -> bool:
    for c in constraints:
        if c.attr not in attrs or not _holds(attrs[c.attr], c.op, c.value):
            return False
    return True


def load_hits(categories: list[str]) -> list[RecordHit]:
    hits = []
    for r in db.list_records():
        if not _category_matches(r["category"], categories):
            continue
        hits.append(
            RecordHit(
                name=r["name"],
                category=r["category"],
                attrs=json.loads(r["attrs_json"]),
                doc_id=r["doc_id"],
                table_id=r["table_id"],
                page_no=r["page_no"],
                raw_text=r["raw_text"],
            )
        )
    return hits


def run_filter(plan: QueryPlan) -> tuple[list[RecordHit], list[RecordHit]]:
    """→ (vyhovujúce, najbližšie-nevyhovujúce pre poctivú odpoveď pri nule)."""
    candidates = load_hits(plan.categories)
    matched = [h for h in candidates if _passes(h.attrs, plan.constraints)]

    sort_attr = plan.maximize or (plan.constraints[0].attr if plan.constraints else "kcal")
    reverse = plan.maximize is not None
    matched.sort(key=lambda h: h.attrs.get(sort_attr, 0), reverse=reverse)

    nearest: list[RecordHit] = []
    if not matched and plan.constraints:
        main = plan.constraints[0]
        with_attr = [h for h in candidates if main.attr in h.attrs]
        with_attr.sort(key=lambda h: abs(h.attrs[main.attr] - main.value))
        nearest = with_attr[:3]
    return matched, nearest


def hits_to_sources(hits: list[RecordHit]) -> list[Source]:
    """Zoskupí záznamy podľa tabuľky → očíslované zdroje s dokumentom a stranou."""
    sources: list[Source] = []
    seen: dict[str, int] = {}
    for h in hits:
        if h.table_id in seen:
            continue
        doc = db.get_document(h.doc_id)
        table = db.get_table(h.table_id)
        n = len(sources) + 1
        seen[h.table_id] = n
        sources.append(
            Source(
                n=n,
                chunk_id=f"record-table:{h.table_id}",
                doc_id=h.doc_id,
                filename=doc["filename"] if doc else h.doc_id,
                page_from=h.page_no or 1,
                page_to=h.page_no or 1,
                type="table_summary",
                table_id=h.table_id,
                text=(table["summary"] if table else "") or "",
                snippet=(h.category or "") + ": " + h.raw_text[:150],
            )
        )
    return sources


def format_hit(h: RecordHit, source_n: int) -> str:
    attrs = ", ".join(
        f"{k}={v:g}" for k, v in sorted(h.attrs.items()) if not k.startswith("val_")
    ) or ", ".join(f"{k}={v:g}" for k, v in sorted(h.attrs.items()))
    cat = f" ({h.category})" if h.category else ""
    return f"- {h.name}{cat}: {attrs} [{source_n}]"


def format_filter_answer(
    plan: QueryPlan, matched: list[RecordHit], nearest: list[RecordHit], sources: list[Source]
) -> str:
    """Deterministická odpoveď pre filter mód — žiadny LLM, žiadne zlyhanie.

    (Model dostal zoznam 8 položiek a vypísal jednu — formulovanie zoznamov
    malému modelu nezverujeme.)
    """
    table_n = {s.table_id: s.n for s in sources}
    conds = "; ".join(
        f"{ATTR_LABELS.get(c.attr, c.attr)} {c.op} {c.value:g}" for c in plan.constraints
    )
    cats = f" (kategória: {', '.join(plan.categories)})" if plan.categories else ""

    if matched:
        lines = [f"Podmienkam {conds}{cats} vyhovuje {len(matched)} položiek:", ""]
        lines += [format_hit(h, table_n.get(h.table_id, 1)) for h in matched[:30]]
        if len(matched) > 30:
            lines.append(f"… a ďalších {len(matched) - 30}.")
        return "\n".join(lines)

    lines = [f"Podmienkam {conds}{cats} nevyhovuje žiadna položka."]
    if nearest:
        lines += ["", "Najbližšie hodnoty (limit NESPĹŇAJÚ):"]
        lines += [format_hit(h, table_n.get(h.table_id, 1)) for h in nearest]
    return "\n".join(lines)


ATTR_LABELS = {"kcal": "kcal", "b": "bielkoviny", "s": "sacharidy", "t": "tuky"}


def build_filter_context(
    plan: QueryPlan, matched: list[RecordHit], nearest: list[RecordHit]
) -> tuple[str, list[Source]]:
    """→ (blok presných výsledkov pre LLM, zdroje na citovanie)."""
    sources = hits_to_sources(matched or nearest)
    table_n = {s.table_id: s.n for s in sources}

    lines = [
        "PRESNÉ výsledky filtra (spočítané kódom — NEPREPOČÍTAVAJ, NEPRIDÁVAJ položky):"
    ]
    conds = "; ".join(f"{c.attr} {c.op} {c.value:g}" for c in plan.constraints)
    if conds:
        lines.append(f"Podmienky: {conds}")
    if plan.categories:
        lines.append(f"Kategórie: {', '.join(plan.categories)}")

    if matched:
        lines.append(f"Vyhovuje {len(matched)} položiek:")
        lines += [format_hit(h, table_n.get(h.table_id, 1)) for h in matched[:30]]
    else:
        lines.append("Podmienkam nevyhovuje ŽIADNA položka.")
        if nearest:
            lines.append("Najbližšie hodnoty (NEvyhovujú podmienke):")
            lines += [format_hit(h, table_n.get(h.table_id, 1)) for h in nearest]
    return "\n".join(lines), sources

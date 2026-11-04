"""Exact filter over records — code compares the numbers, the LLM only phrases.

Returns a structured result + sources (table → document + page) so that
citations work the same as in classic RAG.
"""

import json
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from app import db
from app.auth import allowed_doc_ids
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
    """Exact after normalisation, otherwise token_set — partial_ratio matched a generic
    word ('varianty') and let foreign categories through (Oběd ↔ Večeře)."""
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


def load_hits(categories: list[str], doc_ids: list[str] | None = None) -> list[RecordHit]:
    hits = []
    allowed = allowed_doc_ids()
    for r in db.list_records():
        if allowed is not None and r["doc_id"] not in allowed:
            continue
        if not _category_matches(r["category"], categories):
            continue
        if doc_ids and r["doc_id"] not in doc_ids:
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
    """→ (matching, closest non-matching for an honest answer when there are none)."""
    candidates = load_hits(plan.categories, plan.doc_ids)
    if plan.doc_ids and not any(c.attr in h.attrs for h in candidates for c in plan.constraints):
        # the document from the question lacks that attribute (e.g. „Agátová" → contract)
        # → whole library
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
    """Groups records by table → numbered sources with document and page."""
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


def attr_labels() -> dict[str, str]:
    """key → „Pevnosť [MPa]" from the registry; meal-plan macros have fixed labels."""
    labels = dict(ATTR_LABELS)
    for a in db.list_record_attrs():
        labels.setdefault(a["key"], f"{a['label']} [{a['unit']}]" if a["unit"] else a["label"])
    return labels


def _fmt_value(v: float) -> str:
    """12400.0 → „12 400", 36.4 → „36.4" — thousands as in Slovak documents."""
    if v == int(v) and abs(v) >= 10_000:
        return f"{int(v):,}".replace(",", " ")
    return f"{v:g}"


def format_hit(h: RecordHit, source_n: int, labels: dict[str, str] | None = None) -> str:
    labels = labels or ATTR_LABELS
    shown = {k: v for k, v in sorted(h.attrs.items()) if not k.startswith("val_")} or h.attrs
    attrs = ", ".join(
        f"{k}={v:g}" if k in ("kcal", "b", "s", "t") else f"{labels.get(k, k)} {_fmt_value(v)}"
        for k, v in shown.items()
    )
    cat = f" ({h.category})" if h.category else ""
    return f"- {h.name}{cat}: {attrs} [{source_n}]"


def format_filter_answer(
    plan: QueryPlan, matched: list[RecordHit], nearest: list[RecordHit], sources: list[Source]
) -> str:
    """Deterministic answer for filter mode — no LLM, no failure.

    (The model was given a list of 8 items and printed one — we do not entrust
    phrasing lists to the small model.)
    """
    table_n = {s.table_id: s.n for s in sources}
    labels = attr_labels()
    conds = "; ".join(
        f"{labels.get(c.attr, c.attr)} {c.op} {_fmt_value(c.value)}" for c in plan.constraints
    )
    cats = f" (kategória: {', '.join(plan.categories)})" if plan.categories else ""

    if matched:
        lines = [f"Podmienkam {conds}{cats} vyhovuje {len(matched)} položiek:", ""]
        lines += [format_hit(h, table_n.get(h.table_id, 1), labels) for h in matched[:30]]
        if len(matched) > 30:
            lines.append(f"… a ďalších {len(matched) - 30}.")
        return "\n".join(lines)

    lines = [f"Podmienkam {conds}{cats} nevyhovuje žiadna položka."]
    if nearest:
        lines += ["", "Najbližšie hodnoty (limit NESPĹŇAJÚ):"]
        lines += [format_hit(h, table_n.get(h.table_id, 1), labels) for h in nearest]
    return "\n".join(lines)


ATTR_LABELS = {"kcal": "kcal", "b": "bielkoviny", "s": "sacharidy", "t": "tuky"}


def build_filter_context(
    plan: QueryPlan, matched: list[RecordHit], nearest: list[RecordHit]
) -> tuple[str, list[Source]]:
    """→ (block of exact results for the LLM, sources to cite)."""
    sources = hits_to_sources(matched or nearest)
    table_n = {s.table_id: s.n for s in sources}

    lines = ["PRESNÉ výsledky filtra (spočítané kódom — NEPREPOČÍTAVAJ, NEPRIDÁVAJ položky):"]
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

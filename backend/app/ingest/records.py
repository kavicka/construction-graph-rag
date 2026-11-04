"""Records store — structured records from table rows.

A third index next to vectors and the graph. Filter/aggregation questions („obed do 870 kcal",
„vzorky s pevnosťou pod 38 MPa", „položky rozpočtu nad 400 000 EUR") are solved by code
over these records — the LLM never compares numbers.

Two sources of attributes:
- numeric columns by header („Pevnosť (MPa)" → pevnost [MPa]) — any domain,
- cells with units („829 kcal 74b, 84s, 17t") — menus and similar free-form notation.
The router uses the attribute registry (label + unit) to recognise conditions.

Extraction is deterministic (regex + heuristics over headers), no LLM:
repeatable, testable, zero hallucinations.
"""

import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from app import db

logger = logging.getLogger(__name__)

# "548 kcal 61b, 49s, 10t" — kcal + abbreviated macros (b=protein, s=carbohydrates, t=fat)
KCAL_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:kcal|kkal)\b", re.IGNORECASE)
MACRO_SHORT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([bst])\b", re.IGNORECASE)
# general units: "55 g bílkovin", "500ml", "0.5 %"
UNIT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(g|kg|mg|ml|l|%|ks)\b", re.IGNORECASE)
NAMEISH_HEADERS = (
    "nazev",
    "nazov",
    "name",
    "jidlo",
    "jedlo",
    "polozka",
    "item",
    "produkt",
    "subdodavatel",
    "vzorka",
    "sample",
    "objekt",
    "sensor",
    "claim",
)
# the whole cell is a number: „1 742 300", „41.2", „0,5", „12 %"
NUMERIC_CELL = re.compile(r"^\s*(-?\d{1,3}(?:[ \u00a0]\d{3})+|-?\d+)([.,]\d+)?\s*%?\s*$")
HEADER_UNIT = re.compile(r"\(([^)]{1,15})\)\s*$")
CODE_HEADERS = (
    "ico",
    "dic",
    "ic_dph",
    "cislo",
    "por_c",
    "poradie",
    "id",
    "rok",
    "year",
    "no",
    "nr",
    "number",
    "kod",
    "code",
)
NUMERIC_HINT_HEADERS = {"kcal": "kcal", "macra": None, "makra": None, "energie": "kcal"}


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def parse_cell_attrs(text: str) -> dict[str, float]:
    """Extract numeric attributes from a cell. kcal + b/s/t abbreviations only if kcal is present
    or they form a macro triple (guards against false 'b' in ordinary text)."""
    attrs: dict[str, float] = {}
    kcal = KCAL_RE.search(text)
    if kcal:
        attrs["kcal"] = _num(kcal.group(1))
    shorts = MACRO_SHORT_RE.findall(text)
    if shorts and (kcal or len(shorts) >= 2):
        for value, key in shorts:
            attrs.setdefault(key.lower(), _num(value))
    for value, unit in UNIT_RE.findall(text):
        key = f"val_{unit.lower()}"
        attrs.setdefault(key, _num(value))
    return attrs


def parse_numeric_cell(text: str) -> float | None:
    m = NUMERIC_CELL.match(text)
    if not m:
        return None
    integer = re.sub(r"[ \u00a0]", "", m.group(1))
    return float(integer + (m.group(2) or "").replace(",", "."))


def header_attr(header: str) -> tuple[str, str, str | None] | None:
    """„Pevnosť (MPa)" → ("pevnost", "Pevnosť", "MPa"); identifiers (IČO, č.) → None."""
    unit_m = HEADER_UNIT.search(header)
    unit = unit_m.group(1).strip() if unit_m else None
    label = HEADER_UNIT.sub("", header).strip(" :")
    key = re.sub(r"[^a-z0-9]+", "_", _norm(label)).strip("_")
    # single-letter („Č.") and identifier headers are not measurable attributes
    if len(key) < 2 or key in CODE_HEADERS or len(key) > 40:
        return None
    return key, label, unit


def _read_table_rows(meta: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """→ (headers, rows) — from parquet; markdown fallback."""
    if meta["parquet_path"] and Path(meta["parquet_path"]).exists():
        import pyarrow.parquet as pq

        table = pq.read_table(meta["parquet_path"])
        headers = [str(c) for c in table.column_names]
        rows = [["" if v is None else str(v) for v in row.values()] for row in table.to_pylist()]
        return headers, rows

    md_path = Path(meta["markdown_path"])
    if not md_path.exists():
        return [], []
    lines = [ln for ln in md_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    parsed = [[c.strip() for c in ln.strip("|").split("|")] for ln in lines]
    parsed = [p for p in parsed if not all(set(c) <= {"-", ":", " "} for c in p)]
    if not parsed:
        return [], []
    return parsed[0], parsed[1:]


def _name_column(headers: list[str], rows: list[list[str]]) -> int | None:
    for i, h in enumerate(headers):
        if any(k in _norm(h) for k in NAMEISH_HEADERS):
            return i
    # fallback: first column where non-empty non-numeric text predominates
    for i in range(len(headers)):
        cells = [r[i] for r in rows if i < len(r) and r[i].strip()]
        if cells and sum(1 for c in cells if not c.replace(".", "").isdigit()) > len(cells) / 2:
            return i
    return None


def _numeric_columns(
    headers: list[str], rows: list[list[str]], name_col: int | None
) -> dict[int, tuple[str, str, str | None]]:
    """Columns where most non-empty cells are plain numbers and the header is not a code."""
    out: dict[int, tuple[str, str, str | None]] = {}
    for i, header in enumerate(headers):
        if i == name_col:
            continue
        cells = [r[i] for r in rows if i < len(r) and r[i].strip()]
        numeric = [c for c in cells if parse_numeric_cell(c) is not None]
        attr = header_attr(header)
        if attr and cells and len(numeric) >= max(1, len(cells) * 0.6):
            out[i] = attr
    return out


def extract_records(doc_id: str) -> int:
    """Break the document's tables into records. Idempotent (replace)."""
    out: list[dict[str, Any]] = []
    registry: dict[str, tuple[str, str | None]] = {}
    for meta in db.list_tables(doc_id):
        headers, rows = _read_table_rows(meta)
        if not rows:
            continue
        name_col = _name_column(headers, rows)
        category = (meta["caption"] or "").strip() or None
        numeric_cols = _numeric_columns(headers, rows, name_col)
        for key, label, unit in numeric_cols.values():
            registry.setdefault(key, (label, unit))

        for row in rows:
            attrs: dict[str, float] = {}
            for i, (key, _label, _unit) in numeric_cols.items():
                value = parse_numeric_cell(row[i]) if i < len(row) else None
                if value is not None:
                    attrs[key] = value
            for cell in row:
                for k, v in parse_cell_attrs(cell).items():
                    attrs.setdefault(k, v)
            raw = " | ".join(c for c in row if c.strip())
            if not attrs or not raw:
                continue  # a row without numbers is of no interest to a filter
            name = ""
            if name_col is not None and name_col < len(row):
                name = row[name_col].strip()
            if not name:
                name = max(row, key=lambda c: len(c.strip()), default="").strip()[:80]
            if not name:
                continue
            out.append(
                {
                    "id": uuid.uuid4().hex,
                    "table_id": meta["id"],
                    "page_no": meta["page_no"],
                    "category": category,
                    "name": name[:120],
                    "attrs_json": json.dumps(attrs, ensure_ascii=False),
                    "raw_text": raw[:500],
                }
            )
    # dedup — a table continuing on the next page produces duplicate rows
    seen: set[tuple[str, str | None, str]] = set()
    unique: list[dict[str, Any]] = []
    for r in out:
        dedup_key = (_norm(r["name"]), r["category"], r["attrs_json"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        unique.append(r)
    stored = db.replace_document_records(doc_id, unique)
    db.replace_record_attrs(doc_id, [(key, label, unit) for key, (label, unit) in registry.items()])
    logger.info("records doc %s: %s záznamov, atribúty %s", doc_id, stored, sorted(registry))
    return stored

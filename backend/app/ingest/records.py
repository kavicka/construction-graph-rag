"""Records store — štruktúrované záznamy z riadkov tabuliek.

Tretí index popri vektoroch a grafe. Filter/agregačné otázky („obed do 870 kcal")
sa riešia SQL-kom nad týmito záznamami — LLM čísla nikdy neporovnáva.

Extrakcia je deterministická (regex + heuristiky nad hlavičkami), žiadny LLM:
opakovateľná, testovateľná, nula halucinácií.
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

# "548 kcal 61b, 49s, 10t" — kcal + skratkové makrá (b=bielkoviny, s=sacharidy, t=tuky)
KCAL_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:kcal|kkal)\b", re.IGNORECASE)
MACRO_SHORT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*([bst])\b", re.IGNORECASE)
# všeobecné jednotky: "55 g bílkovin", "500ml", "0.5 %"
UNIT_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(g|kg|mg|ml|l|%|ks)\b", re.IGNORECASE
)
NAMEISH_HEADERS = ("nazev", "nazov", "name", "jidlo", "jedlo", "polozka", "item", "produkt")
NUMERIC_HINT_HEADERS = {"kcal": "kcal", "macra": None, "makra": None, "energie": "kcal"}


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold().strip())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def parse_cell_attrs(text: str) -> dict[str, float]:
    """Vytiahne číselné atribúty z bunky. kcal + b/s/t skratky len ak je pri nich kcal
    alebo tvoria makro-trojicu (ochrana pred falošnými 'b' v bežnom texte)."""
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


def _read_table_rows(meta: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """→ (hlavičky, riadky) — z parquetu; fallback markdown."""
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
    # fallback: prvý stĺpec, kde prevažuje neprázdny nečíselný text
    for i in range(len(headers)):
        cells = [r[i] for r in rows if i < len(r) and r[i].strip()]
        if cells and sum(1 for c in cells if not c.replace(".", "").isdigit()) > len(cells) / 2:
            return i
    return None


def extract_records(doc_id: str) -> int:
    """Rozloží tabuľky dokumentu na záznamy. Idempotentné (replace)."""
    out: list[dict[str, Any]] = []
    for meta in db.list_tables(doc_id):
        headers, rows = _read_table_rows(meta)
        if not rows:
            continue
        name_col = _name_column(headers, rows)
        category = (meta["caption"] or "").strip() or None

        for row in rows:
            attrs: dict[str, float] = {}
            for cell in row:
                for k, v in parse_cell_attrs(cell).items():
                    attrs.setdefault(k, v)
            raw = " | ".join(c for c in row if c.strip())
            if not attrs or not raw:
                continue  # riadok bez čísel filter nezaujíma
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
    # dedup — tabuľka pokračujúca na ďalšej strane vyrába duplicitné riadky
    seen: set[tuple[str, str | None, str]] = set()
    unique: list[dict[str, Any]] = []
    for r in out:
        key = (_norm(r["name"]), r["category"], r["attrs_json"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    stored = db.replace_document_records(doc_id, unique)
    logger.info("records doc %s: %s záznamov", doc_id, stored)
    return stored

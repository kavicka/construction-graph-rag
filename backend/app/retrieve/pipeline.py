"""Retrieval pipeline (query time).

Tok: dopyt → dense + BM25 + grafová expanzia (flag RAG_GRAPH_EXPANSION)
→ RRF fúzia → table routing → očíslované zdroje pre prompt.

Graf vetva: expanzia entít z top seedov cez SAME_AS klaster; pri relačnej otázke
navyše graf-first dôkazy konceptov spomenutých priamo v otázke.

Table routing: ak hit je table_summary, do kontextu ide sumár + PLNÁ tabuľka
v markdowne (s capom) — presné čísla pochádzajú z parsera, nie z LLM.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app import db
from app.config import get_settings
from app.index.embedder import get_embedder
from app.index.lancedb_store import search_dense, search_fts
from app.retrieve.fuse import rrf_fuse
from app.retrieve.graph_expand import (
    concept_evidence_chunks,
    expand_via_graph,
    is_relational_query,
    match_query_concepts,
)

MAX_CONTEXT_CHARS = 14_000  # ≈ 3,5k tokenov — disciplína KV cache
TABLE_MD_CAP = 6_000
CANDIDATES = 24


@dataclass
class Source:
    n: int
    chunk_id: str
    doc_id: str
    filename: str
    page_from: int
    page_to: int
    type: str
    table_id: str | None
    text: str  # čo ide do promptu
    snippet: str  # krátky náhľad pre UI

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("text")
        return d


def _table_context(table_id: str, summary_text: str) -> str:
    meta = db.get_table(table_id)
    if meta is None:
        return summary_text
    md_path = Path(meta["markdown_path"])
    markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    if len(markdown) > TABLE_MD_CAP:
        # hlavička + prvé riadky; presné čísla nad rámec capu rieši TableViewer/parquet
        markdown = markdown[:TABLE_MD_CAP] + "\n… (tabuľka skrátená)"
    return f"{summary_text}\n\nCelá tabuľka:\n{markdown}"


def _graph_candidates(query: str, dense: list[dict], fts: list[dict]) -> list[dict]:  # type: ignore[type-arg]
    """Tretí zoznam do fúzie: grafová expanzia seedov + graf-first pri relačnej otázke."""
    seed_ids = [r["chunk_id"] for r in rrf_fuse([dense, fts], top_k=6)]
    rows = expand_via_graph(seed_ids, cap=10)
    if is_relational_query(query):
        concepts = match_query_concepts(query)
        rows = concept_evidence_chunks(concepts, cap=8) + rows
    seen: set[str] = set()
    unique: list[dict] = []  # type: ignore[type-arg]
    for r in rows:
        if r["chunk_id"] not in seen:
            seen.add(r["chunk_id"])
            unique.append(r)
    return unique


def retrieve_sources(query: str, k: int = 8) -> list[Source]:
    vector = get_embedder().embed_query(query)
    dense = search_dense(vector, k=CANDIDATES)
    fts = search_fts(query, k=CANDIDATES)

    lists = [dense, fts]
    if get_settings().graph_expansion:
        graph_rows = _graph_candidates(query, dense, fts)
        if graph_rows:
            lists.append(graph_rows)
    fused = rrf_fuse(lists, top_k=k)

    sources: list[Source] = []
    budget = MAX_CONTEXT_CHARS
    for i, row in enumerate(fused, start=1):
        doc = db.get_document(row["doc_id"])
        filename = doc["filename"] if doc else row["doc_id"]

        if row["type"] == "table_summary" and row["table_id"]:
            text = _table_context(row["table_id"], row["text"])
        else:
            text = row["text"]

        if len(text) > budget:
            if len(sources) >= 3:
                continue  # základné pokrytie máme, ďalší veľký zdroj preskoč
            text = text[:budget]
        budget -= len(text)

        sources.append(
            Source(
                n=i,
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                filename=filename,
                page_from=row["page_from"],
                page_to=row["page_to"],
                type=row["type"],
                table_id=row["table_id"],
                text=text,
                snippet=row["raw_text"][:200],
            )
        )
        if budget <= 0:
            break
    # čísla zdrojov po filtrácii preindexuj na 1..n (model cituje presne tieto)
    for idx, s in enumerate(sources, start=1):
        s.n = idx
    return sources


SYSTEM_PROMPT = """Si asistent nad lokálnou knižnicou PDF dokumentov. Pravidlá:
- Odpovedaj VÝHRADNE z poskytnutých zdrojov. Ak odpoveď v zdrojoch nie je, \
povedz to jednou vetou a NEPRIDÁVAJ žiadnu citáciu.
- Odpovedaj v jazyku otázky.
- Za každé tvrdenie pridaj citáciu v tvare [n], kde n je číslo konkrétneho zdroja.
- Čísla z tabuliek uvádzaj presne tak, ako sú v zdroji — nezaokrúhľuj, nedopočítavaj.
- Buď vecný a stručný."""


def build_messages(query: str, sources: list[Source]) -> list[dict[str, str]]:
    blocks = [
        f"[{s.n}] ({s.filename}, str. {s.page_from}"
        + (f"–{s.page_to}" if s.page_to != s.page_from else "")
        + f"):\n{s.text}"
        for s in sources
    ]
    context = "\n\n".join(blocks) if blocks else "(žiadne zdroje sa nenašli)"
    user = f"Zdroje:\n{context}\n\nOtázka: {query}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def sources_to_json(sources: list[Source]) -> str:
    return json.dumps([s.to_json() for s in sources], ensure_ascii=False)

"""Štruktúrne vedomý chunking nad DoclingDocument.

HybridChunker rešpektuje sekcie/nadpisy a merguje malé susedné kusy do limitu tokenov.
Tabuľky sa z textových chunkov vyhadzujú — idú vlastnou vetvou ako LLM sumáre
(embedding surových čísel nemá sémantický signál).
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app import db
from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync

logger = logging.getLogger(__name__)

SUMMARY_SYSTEM = (
    "Si asistent na popis tabuliek z dokumentov. Odpovedáš výhradne zhrnutím, "
    "bez úvodných fráz. Píš v jazyku, v akom je tabuľka."
)
SUMMARY_PROMPT = """Zhrň nasledujúcu tabuľku 2 až 5 vetami tak, aby sa dala nájsť
vyhľadávaním: čo obsahuje, VYMENUJ názvy položiek/riadkov (do 12), aké má stĺpce
s jednotkami a rozsah kľúčových hodnôt (min–max). Nevymýšľaj čísla, ktoré v tabuľke
nie sú.

Nadpis/kategória tabuľky: {caption}
{table_md}"""

MAX_TABLE_MD_CHARS = 4000


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str  # kontextualizovaný (cesta nadpisov + obsah) — toto sa embeduje a FTS-uje
    raw_text: str
    page_from: int
    page_to: int
    section_path: str
    type: str  # "text" | "table_summary"
    table_id: str | None


@lru_cache
def _get_chunker() -> Any:
    from docling_core.transforms.chunker.hybrid_chunker import HybridChunker
    from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
    from transformers import AutoTokenizer

    settings = get_settings()
    tokenizer = HuggingFaceTokenizer(
        tokenizer=AutoTokenizer.from_pretrained(settings.effective_embedding_model),
        max_tokens=settings.chunk_max_tokens,
    )
    return HybridChunker(tokenizer=tokenizer, merge_peers=True)


def load_document(doc_id: str) -> Any:
    from docling_core.types.doc import DoclingDocument

    path = get_settings().parsed_dir / f"{doc_id}.json"
    return DoclingDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))


def chunk_text_items(doc_id: str, document: Any) -> list[Chunk]:
    from docling_core.types.doc import TableItem

    chunker = _get_chunker()
    chunks: list[Chunk] = []
    for i, ch in enumerate(chunker.chunk(dl_doc=document)):
        items = ch.meta.doc_items
        if items and all(isinstance(it, TableItem) for it in items):
            continue  # tabuľky idú vetvou sumárov
        pages = sorted(
            {prov.page_no for it in items for prov in getattr(it, "prov", [])}
        ) or [1]
        headings = ch.meta.headings or []
        text = chunker.contextualize(chunk=ch)
        if not text.strip():
            continue
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}:t{i}",
                doc_id=doc_id,
                text=text,
                raw_text=ch.text,
                page_from=pages[0],
                page_to=pages[-1],
                section_path=" / ".join(headings),
                type="text",
                table_id=None,
            )
        )
    return chunks


def _fallback_summary(caption: str | None, markdown: str, n_rows: int, n_cols: int) -> str:
    header = markdown.splitlines()[0] if markdown else ""
    cap = f"{caption}. " if caption else ""
    return f"{cap}Tabuľka {n_rows}×{n_cols}. Stĺpce: {header.strip('| ')}"


def summarize_table(caption: str | None, markdown: str, n_rows: int, n_cols: int) -> str:
    settings = get_settings()
    prompt = SUMMARY_PROMPT.format(
        caption=caption or "", table_md=markdown[:MAX_TABLE_MD_CHARS]
    )
    try:
        # sumáre generuje answer model — 1.7b dával generické nerozlíšiteľné sumáre,
        # kvalita embedovaného sumáru priamo určuje, či retrieval tabuľku nájde
        summary = generate_sync(
            settings.answer_model, prompt, system=SUMMARY_SYSTEM, num_ctx=8192
        )
        if summary:
            return summary
    except OllamaError:
        logger.warning("LLM sumár tabuľky zlyhal — deterministický fallback")
    return _fallback_summary(caption, markdown, n_rows, n_cols)


def build_table_chunks(doc_id: str) -> list[Chunk]:
    """Pre každú tabuľku dokumentu: LLM sumár → uloží do DB → chunk na embedovanie."""
    from pathlib import Path

    chunks: list[Chunk] = []
    for t in db.list_tables(doc_id):
        md_path = Path(t["markdown_path"])
        markdown = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        summary = summarize_table(t["caption"], markdown, t["n_rows"], t["n_cols"])
        db.set_table_summary(t["id"], summary)

        header = markdown.splitlines()[0].strip("| ") if markdown else ""
        embed_text = "\n".join(filter(None, [t["caption"], summary, f"Stĺpce: {header}"]))
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}:table:{t['id']}",
                doc_id=doc_id,
                text=embed_text,
                raw_text=summary,
                page_from=t["page_no"] or 1,
                page_to=t["page_no"] or 1,
                section_path=t["caption"] or "",
                type="table_summary",
                table_id=t["id"],
            )
        )
    return chunks

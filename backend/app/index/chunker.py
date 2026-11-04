"""Štruktúrne vedomý chunking nad DoclingDocument.

HybridChunker rešpektuje sekcie/nadpisy a merguje malé susedné kusy do limitu tokenov.
Tabuľky sa z textových chunkov vyhadzujú — idú vlastnou vetvou ako LLM sumáre
(embedding surových čísel nemá sémantický signál).
"""

import json
import logging
from dataclasses import dataclass, field
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
    # [strana, x0, y0, x1, y1] — 0..1 relatívne k strane, počiatok vľavo hore (zvýraznenie v PDF)
    boxes: list[list[float]] = field(default_factory=list)


MAX_BOXES_PER_CHUNK = 16


def item_boxes(document: Any, items: list[Any]) -> list[list[float]]:
    """Bounding boxy Docling položiek prepočítané na relatívne súradnice strany."""
    boxes: list[list[float]] = []
    for it in items:
        for prov in getattr(it, "prov", []) or []:
            page = document.pages.get(prov.page_no)
            if page is None or not page.size.width or not page.size.height:
                continue
            w, h = page.size.width, page.size.height
            bb = prov.bbox.to_top_left_origin(page_height=h)
            boxes.append(
                [
                    float(prov.page_no),
                    round(max(0.0, bb.l / w), 4),
                    round(max(0.0, bb.t / h), 4),
                    round(min(1.0, bb.r / w), 4),
                    round(min(1.0, bb.b / h), 4),
                ]
            )
            if len(boxes) >= MAX_BOXES_PER_CHUNK:
                return boxes
    return boxes


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
        pages = sorted({prov.page_no for it in items for prov in getattr(it, "prov", [])}) or [1]
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
                boxes=item_boxes(document, items),
            )
        )
    return chunks


CONTEXT_DOC_CHARS = 6000  # začiatok dokumentu ako kontext — malý model, malé okno

CONTEXT_PROMPT = """<document>
{document}
</document>
Tu je úsek, ktorý chceme zasadiť do kontextu celého dokumentu:
<chunk>
{chunk}
</chunk>
Napíš 1–2 krátke vety, ktoré zasadia úsek do kontextu dokumentu (o aký dokument ide,
ktorého projektu/subjektu sa týka, ktorej časti), aby sa úsek dal lepšie nájsť
vyhľadávaním. Píš v jazyku dokumentu. Odpovedz LEN tým kontextom."""


def _context_key(document_head: str, chunk_text: str, model: str) -> str:
    import hashlib

    return hashlib.sha256(f"{model}\0{document_head}\0{chunk_text}".encode()).hexdigest()


def contextualize_chunks(doc_id: str, document: Any, chunks: list[Chunk]) -> int:
    """Anthropic Contextual Retrieval: pred embedding aj BM25 sa ku chunku pridá
    LLM kontext („Zmluva o dielo Agátová, článok o zmluvnej pokute“). Rieši chunky,
    ktoré samy osebe nehovoria, ku ktorému projektu/dokumentu patria.
    Výsledky sa cachujú podľa hash(dokument, chunk, model) — reindex je zadarmo."""
    settings = get_settings()
    doc_row = db.get_document(doc_id)
    title = doc_row["filename"] if doc_row else doc_id
    head = f"Súbor: {title}\n" + document.export_to_markdown()[:CONTEXT_DOC_CHARS]
    added = 0
    for chunk in chunks:
        key = _context_key(head, chunk.raw_text, settings.answer_model)
        context = db.get_chunk_context(key)
        if context is None:
            try:
                context = generate_sync(
                    settings.answer_model,
                    CONTEXT_PROMPT.format(document=head, chunk=chunk.raw_text[:2000]),
                    temperature=0.0,
                    num_ctx=8192,
                ).strip()
            except OllamaError:
                logger.warning("kontext chunku %s zlyhal — chunk ostáva bez neho", chunk.chunk_id)
                continue
            db.set_chunk_context(key, context)
        if context:
            chunk.text = f"{context}\n\n{chunk.text}"
            added += 1
    return added


def _fallback_summary(caption: str | None, markdown: str, n_rows: int, n_cols: int) -> str:
    header = markdown.splitlines()[0] if markdown else ""
    cap = f"{caption}. " if caption else ""
    return f"{cap}Tabuľka {n_rows}×{n_cols}. Stĺpce: {header.strip('| ')}"


def summarize_table(caption: str | None, markdown: str, n_rows: int, n_cols: int) -> str:
    settings = get_settings()
    prompt = SUMMARY_PROMPT.format(caption=caption or "", table_md=markdown[:MAX_TABLE_MD_CHARS])
    try:
        # sumáre generuje answer model — 1.7b dával generické nerozlíšiteľné sumáre,
        # kvalita embedovaného sumáru priamo určuje, či retrieval tabuľku nájde
        summary = generate_sync(settings.answer_model, prompt, system=SUMMARY_SYSTEM, num_ctx=8192)
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
                boxes=json.loads(t["boxes_json"]) if t.get("boxes_json") else [],
            )
        )
    return chunks

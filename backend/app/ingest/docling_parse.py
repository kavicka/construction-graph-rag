"""Docling parsing — runs in a thread pool (CPU-bound).

One conversion per document: layout + TableFormer + OCR based on detection.
Output: DoclingDocument JSON (lossless), pages metadata, tables (markdown + parquet).
"""

import json
import logging
import uuid
from functools import lru_cache
from typing import Any

from app import db
from app.config import get_settings
from app.ocr import detect_ocr

logger = logging.getLogger(__name__)


@lru_cache
def _get_converter() -> Any:
    """The converter is expensive (ML models) — singleton, lazy import for app startup time."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        OcrMacOptions,
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    settings = get_settings()
    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    # high profile = more accurate (slower) TableFormer — fine on an M4 Pro
    accurate = settings.effective_profile == "high"
    opts.table_structure_options = TableStructureOptions(
        mode=TableFormerMode.ACCURATE if accurate else TableFormerMode.FAST
    )

    ocr = detect_ocr()
    if ocr.engine == "ocrmac":
        opts.do_ocr = True
        opts.ocr_options = OcrMacOptions(lang=ocr.languages)
    elif ocr.engine == "tesseract":
        opts.do_ocr = True
        opts.ocr_options = TesseractCliOcrOptions(lang=ocr.languages)
    else:
        opts.do_ocr = False
        logger.warning("Žiadny OCR engine — skenované PDF ostanú prázdne")

    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.DOCX,
            InputFormat.PPTX,
            InputFormat.XLSX,
            InputFormat.HTML,
            InputFormat.MD,
        ],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)},
    )


def _page_text_lengths(document: Any) -> list[tuple[int, int]]:
    lengths: dict[int, int] = {p: 0 for p in document.pages}
    for item in document.texts:
        for prov in item.prov:
            lengths[prov.page_no] = lengths.get(prov.page_no, 0) + len(item.text)
    return sorted(lengths.items())


MAX_HEADING_PAGE_DISTANCE = 2  # a heading applies at most 2 pages ahead (card layouts)
CARD_PAGE_MAX_CHARS = 60  # a page with a single short text = category card


def _headings_before_tables(document: Any) -> dict[str, str]:
    """A table without a caption gets the nearest heading before it in reading order.

    Design-heavy PDFs (menus, catalogues) have the category as a separate heading
    above the table — without this the table 'does not know' what it contains. The heading is either
    SECTION_HEADER/TITLE, or a lone short text on an otherwise empty page
    ("card" page with a category name). The heading's validity is limited by
    page distance — otherwise it leaks across the whole document.
    """
    from docling_core.types.doc import TableItem
    from docling_core.types.doc.labels import DocItemLabel

    heading_labels = {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}

    page_texts: dict[int, list[str]] = {}
    for t in document.texts:
        for prov in t.prov:
            page_texts.setdefault(prov.page_no, []).append(t.text)
    card_pages = {
        p
        for p, texts in page_texts.items()
        if len(texts) <= 2 and sum(len(x) for x in texts) <= CARD_PAGE_MAX_CHARS
    }

    last: tuple[str, int] | None = None  # (heading, page)
    result: dict[str, str] = {}
    for item, _level in document.iterate_items():
        label = getattr(item, "label", None)
        text = (getattr(item, "text", "") or "").strip()
        prov = getattr(item, "prov", None)
        page = prov[0].page_no if prov else None

        is_heading = label in heading_labels and bool(text)
        is_card_title = (
            page in card_pages
            and bool(text)
            and len(text) <= 45
            and not isinstance(item, TableItem)
        )
        if (is_heading or is_card_title) and page is not None:
            last = (text, page)
        elif (
            isinstance(item, TableItem)
            and last is not None
            and page is not None
            and 0 <= page - last[1] <= MAX_HEADING_PAGE_DISTANCE
        ):
            result[item.self_ref] = last[0]
    return result


def _extract_tables(doc_id: str, document: Any) -> int:
    settings = get_settings()
    count = 0
    heading_map = _headings_before_tables(document)
    for item in document.tables:
        table_id = uuid.uuid4().hex
        page_no = item.prov[0].page_no if item.prov else None
        caption = item.caption_text(doc=document) or heading_map.get(item.self_ref) or None

        md_path = settings.tables_dir / f"{table_id}.md"
        md_path.write_text(item.export_to_markdown(doc=document), encoding="utf-8")

        parquet_path: str | None = None
        try:
            df = item.export_to_dataframe(doc=document)
            pq = settings.tables_dir / f"{table_id}.parquet"
            df.to_parquet(pq)
            parquet_path = str(pq)
        except Exception:
            # markdown is the source of truth for LLM context; parquet is a bonus for exact numbers
            logger.exception("parquet export zlyhal pre tabuľku %s", table_id)

        from app.index.chunker import item_boxes

        db.insert_table(
            table_id,
            doc_id,
            page_no,
            caption,
            item.data.num_rows,
            item.data.num_cols,
            str(md_path),
            parquet_path,
            boxes_json=json.dumps(item_boxes(document, [item])),
        )
        count += 1
    return count


def parse_document(doc_id: str) -> dict[str, int]:
    """Synchronous full parse of a single document (PDF, DOCX, PPTX, XLSX, HTML, MD).
    Call via asyncio.to_thread."""
    settings = get_settings()
    doc = db.get_document(doc_id)
    fmt = (doc or {}).get("format") or "pdf"
    path = settings.pdf_dir / f"{doc_id}.{fmt}"

    result = _get_converter().convert(path)
    document = result.document

    parsed_path = settings.parsed_dir / f"{doc_id}.json"
    parsed_path.write_text(
        json.dumps(document.export_to_dict(), ensure_ascii=False), encoding="utf-8"
    )

    pages = _page_text_lengths(document) or [(1, sum(len(t.text) for t in document.texts))]
    db.set_pages(doc_id, pages)

    db.delete_document_tables(doc_id)
    tables_count = _extract_tables(doc_id, document)

    return {"pages": len(pages), "tables": tables_count}

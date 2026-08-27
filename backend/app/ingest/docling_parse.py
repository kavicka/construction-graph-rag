"""Docling parsing — beží v thread pooli (CPU-bound).

Jeden konvert na dokument: layout + TableFormer + OCR podľa detekcie.
Výstup: DoclingDocument JSON (lossless), pages metadáta, tabuľky (markdown + parquet).
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
    """Konverter je drahý (ML modely) — singleton, lazy import kvôli štartu appky."""
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
    # high profil = presnejší (pomalší) TableFormer — na M4 Pro bez problémov
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
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


def _page_text_lengths(document: Any) -> list[tuple[int, int]]:
    lengths: dict[int, int] = {p: 0 for p in document.pages}
    for item in document.texts:
        for prov in item.prov:
            lengths[prov.page_no] = lengths.get(prov.page_no, 0) + len(item.text)
    return sorted(lengths.items())


def _extract_tables(doc_id: str, document: Any) -> int:
    settings = get_settings()
    count = 0
    for item in document.tables:
        table_id = uuid.uuid4().hex
        page_no = item.prov[0].page_no if item.prov else None
        caption = item.caption_text(doc=document) or None

        md_path = settings.tables_dir / f"{table_id}.md"
        md_path.write_text(item.export_to_markdown(doc=document), encoding="utf-8")

        parquet_path: str | None = None
        try:
            df = item.export_to_dataframe(doc=document)
            pq = settings.tables_dir / f"{table_id}.parquet"
            df.to_parquet(pq)
            parquet_path = str(pq)
        except Exception:
            # markdown je zdroj pravdy pre LLM kontext; parquet je bonus pre presné čísla
            logger.exception("parquet export zlyhal pre tabuľku %s", table_id)

        db.insert_table(
            table_id,
            doc_id,
            page_no,
            caption,
            item.data.num_rows,
            item.data.num_cols,
            str(md_path),
            parquet_path,
        )
        count += 1
    return count


def parse_pdf(doc_id: str) -> dict[str, int]:
    """Synchrónny celý parse jedného dokumentu. Volať cez asyncio.to_thread."""
    settings = get_settings()
    pdf_path = settings.pdf_dir / f"{doc_id}.pdf"

    result = _get_converter().convert(pdf_path)
    document = result.document

    parsed_path = settings.parsed_dir / f"{doc_id}.json"
    parsed_path.write_text(
        json.dumps(document.export_to_dict(), ensure_ascii=False), encoding="utf-8"
    )

    pages = _page_text_lengths(document)
    db.set_pages(doc_id, pages)

    db.delete_document_tables(doc_id)
    tables_count = _extract_tables(doc_id, document)

    return {"pages": len(pages), "tables": tables_count}

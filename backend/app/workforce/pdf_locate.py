"""Presná poloha citátu v PDF — riadky textovej vrstvy, nie celý blok z Doclingu.

Docling niekedy zlúči viac odsekov do jedného bloku; zvýraznenie by potom pokrylo polovicu
strany. Tu sa citát fuzzy zarovná na text strany (pypdfium2) a vrátia sa obdĺžniky
presne tých znakov. Skeny bez textovej vrstvy → None (volajúci použije box z Doclingu).
"""

import logging
from pathlib import Path

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

MIN_SCORE = 85
MAX_RECTS = 12


def _flat(text: str) -> str:
    # rovnaká dĺžka ako originál — indexy sa dajú mapovať späť na znaky strany
    return text.lower().replace("\r", " ").replace("\n", " ")


def locate_quote(pdf_path: Path, page_no: int, quote: str) -> list[list[float]] | None:
    """→ [[strana, x0, y0, x1, y1], …] relatívne 0..1, počiatok vľavo hore; alebo None."""
    if not quote.strip() or not pdf_path.exists():
        return None
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            if not 1 <= page_no <= len(pdf):
                return None
            page = pdf[page_no - 1]
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            if not text.strip():
                return None
            target = " ".join(quote.split()).lower()
            hit = fuzz.partial_ratio_alignment(target, _flat(text))
            if hit is None or hit.score < MIN_SCORE:
                return None
            count = textpage.count_rects(hit.dest_start, hit.dest_end - hit.dest_start)
            boxes: list[list[float]] = []
            for i in range(min(count, MAX_RECTS)):
                left, bottom, right, top = textpage.get_rect(i)
                x0, x1 = left / width, right / width
                y0, y1 = (height - top) / height, (height - bottom) / height  # PDF má počiatok dole
                boxes.append([
                    float(page_no),
                    round(max(0.0, x0), 4), round(max(0.0, y0), 4),
                    round(min(1.0, x1), 4), round(min(1.0, y1), 4),
                ])
            return boxes or None
        finally:
            pdf.close()
    except Exception:  # noqa: BLE001 — zvýraznenie je bonus, import nesmie spadnúť
        logger.warning("citát sa v PDF nepodarilo lokalizovať", exc_info=True)
        return None

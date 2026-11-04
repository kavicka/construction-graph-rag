"""locate_quote: fuzzy alignment of a quote onto the PDF text layer → relative line boxes."""

from pathlib import Path

import pytest
import reportlab
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.workforce import pdf_locate
from app.workforce.pdf_locate import MAX_RECTS, locate_quote

PAGE_W, PAGE_H = A4
LEFT = 72.0
TOP = 760.0
LEADING = 20.0

# Bundled Bitstream Vera has š č ž á ú ý ô ď … (no ľ ť ň ä) — enough for real Slovak words,
# and the built-in Helvetica would turn every one of them into a missing glyph.
_VERA = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"


def _make_pdf(path: Path, pages: list[list[str]]) -> Path:
    pdfmetrics.registerFont(TTFont("Vera", str(_VERA)))
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in pages:
        c.setFont("Vera", 12)
        for i, line in enumerate(lines):
            c.drawString(LEFT, TOP - i * LEADING, line)
        c.showPage()
    c.save()
    return path


@pytest.fixture
def contract(tmp_path: Path) -> Path:
    return _make_pdf(
        tmp_path / "zmluva.pdf",
        [
            [
                "Zmluvná pokuta činí päť percent z ceny diela.",
                "Stavbyvedúci Žilinský dozoruje betonáž stropu.",
                "Záručná doba je šesťdesiat mesiacov od prevzatia.",
            ],
            [
                "Druhá strana obsahuje harmonogram prác.",
                "Dokončenie hrubej stavby je naplánované na október.",
            ],
        ],
    )


def _assert_valid_box(box: list[float], page_no: int) -> None:
    page, x0, y0, x1, y1 = box
    assert page == float(page_no)
    assert 0.0 <= x0 < x1 <= 1.0
    assert 0.0 <= y0 < y1 <= 1.0


def test_single_line_quote_returns_one_relative_box(contract: Path) -> None:
    boxes = locate_quote(contract, 1, "Zmluvná pokuta činí päť percent z ceny diela.")
    assert boxes is not None
    assert len(boxes) == 1
    _assert_valid_box(boxes[0], 1)
    _, x0, y0, x1, y1 = boxes[0]
    # text starts at the left margin; origin of the result is top-left, so line 1 is near the top
    assert x0 == pytest.approx(LEFT / PAGE_W, abs=0.01)
    assert 0.05 < y0 < y1 < 0.12  # baseline 760pt of 842pt → ~0.08 from the top
    assert y1 - y0 < 0.03  # one text line, not half of the page


def test_highlight_covers_only_the_quoted_part(contract: Path) -> None:
    full = locate_quote(contract, 1, "Zmluvná pokuta činí päť percent z ceny diela.")
    part = locate_quote(contract, 1, "päť percent")
    assert full is not None and part is not None
    assert part[0][1] > full[0][1]  # starts to the right of the line start
    assert part[0][3] < full[0][3]  # and ends before the line end


def test_quote_on_second_line_is_below_first_line(contract: Path) -> None:
    first = locate_quote(contract, 1, "Zmluvná pokuta")
    second = locate_quote(contract, 1, "Stavbyvedúci Žilinský dozoruje")
    assert first is not None and second is not None
    assert second[0][2] > first[0][2]


def test_multi_line_quote_returns_one_box_per_line(contract: Path) -> None:
    # the quote is wrapped over two physical lines, with the line break written as a newline
    boxes = locate_quote(contract, 1, "percent z ceny diela.\nStavbyvedúci Žilinský dozoruje")
    assert boxes is not None
    assert len(boxes) >= 2
    for box in boxes:
        _assert_valid_box(box, 1)
    ys = [b[2] for b in boxes]
    assert ys == sorted(ys)  # reading order, top to bottom


def test_quote_whitespace_and_case_are_normalised(contract: Path) -> None:
    boxes = locate_quote(contract, 1, "  ZMLUVNÁ   pokuta\n\tČINÍ  päť  ")
    assert boxes is not None
    assert len(boxes) == 1


def test_diacritics_are_matched(contract: Path) -> None:
    boxes = locate_quote(contract, 1, "Záručná doba je šesťdesiat mesiacov")
    assert boxes is not None
    _assert_valid_box(boxes[0], 1)
    assert boxes[0][2] > locate_quote(contract, 1, "Zmluvná pokuta")[0][2]  # type: ignore[index]


def test_small_ocr_style_typo_still_matches(contract: Path) -> None:
    # one wrong character (OCR noise in the extracted quote) stays above the 85 threshold
    boxes = locate_quote(contract, 1, "Stavbyvedúci Žilinský dozoruje betonaž stropu.")
    assert boxes is not None
    assert len(boxes) == 1


def test_quote_is_located_on_the_requested_page_only(contract: Path) -> None:
    on_page_2 = locate_quote(contract, 2, "Dokončenie hrubej stavby je naplánované na október")
    assert on_page_2 is not None
    assert on_page_2[0][0] == 2.0  # page number is the first element of every box
    # the same sentence asked for on page 1 is simply not there
    assert locate_quote(contract, 1, "Dokončenie hrubej stavby je naplánované na október") is None


def test_unrelated_quote_below_threshold_returns_none(contract: Path) -> None:
    assert locate_quote(contract, 1, "Kvantová chromodynamika popisuje silnú interakciu") is None


@pytest.mark.parametrize("quote", ["", "   ", "\n\t "])
def test_blank_quote_returns_none(contract: Path, quote: str) -> None:
    assert locate_quote(contract, 1, quote) is None


@pytest.mark.parametrize("page_no", [0, -1, 3, 99])
def test_page_out_of_range_returns_none(contract: Path, page_no: int) -> None:
    assert locate_quote(contract, page_no, "Zmluvná pokuta") is None


def test_missing_file_returns_none(tmp_path: Path) -> None:
    assert locate_quote(tmp_path / "neexistuje.pdf", 1, "text") is None


def test_corrupt_pdf_returns_none_without_raising(tmp_path: Path) -> None:
    broken = tmp_path / "rozbite.pdf"
    broken.write_bytes(b"%PDF-1.4 toto nie je platny subor")
    assert locate_quote(broken, 1, "text") is None


def test_scan_without_text_layer_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "sken.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.rect(72, 600, 300, 100, fill=1)  # only vector graphics, no text at all
    c.showPage()
    c.save()
    assert locate_quote(path, 1, "Zmluvná pokuta") is None


def test_box_count_is_capped(tmp_path: Path) -> None:
    lines = [f"Riadok číslo {i:02d} obsahuje opakujúci sa text zmluvy." for i in range(25)]
    path = _make_pdf(tmp_path / "dlhy.pdf", [lines])
    quote = " ".join(lines)
    boxes = locate_quote(path, 1, quote)
    assert boxes is not None
    assert len(boxes) == MAX_RECTS


def test_flat_keeps_length_so_indices_map_back() -> None:
    text = "Prvý riadok\r\nDruhý\nriadok"
    flat = pdf_locate._flat(text)
    assert len(flat) == len(text)
    assert "\n" not in flat and "\r" not in flat
    assert flat == "prvý riadok  druhý riadok"

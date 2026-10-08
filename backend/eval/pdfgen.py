"""Spoločné renderovanie eval PDF cez reportlab.

Predvolený reportlab font (Helvetica, WinAnsi) nemá č, ľ, ť, ŕ, ĺ — PDF by obsahovalo
diery a Docling by z neho čítal „zh nn a“ namiesto „zhŕňa“. Preto TTF s plnou latinkou.
"""

from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

FONT_CANDIDATES = [
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
]

# blok stránky: ("h", nadpis) | ("p", odsek) | ("table", riadky)
Block = tuple[str, str] | tuple[str, list[list[str]]]
Page = Sequence[Block]


@lru_cache
def _fonts() -> tuple[str, str]:
    for regular, bold in FONT_CANDIDATES:
        if Path(regular).exists() and Path(bold).exists():
            pdfmetrics.registerFont(TTFont("EvalSans", regular))
            pdfmetrics.registerFont(TTFont("EvalSans-Bold", bold))
            return "EvalSans", "EvalSans-Bold"
    raise RuntimeError(
        "Nenašiel som TTF font s latinkou (Arial / DejaVu Sans) — eval PDF by stratili diakritiku"
    )


@lru_cache
def _styles() -> dict[str, ParagraphStyle]:
    regular, bold = _fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=bold, fontSize=18),
        "h": ParagraphStyle("h", parent=base["Heading2"], fontName=bold, fontSize=13),
        "p": ParagraphStyle("p", parent=base["Normal"], fontName=regular, fontSize=10,
                            leading=14),
    }


def _table(rows: list[list[str]]) -> Table:
    regular, bold = _fonts()
    t = Table(rows, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), regular),
                ("FONTNAME", (0, 0), (-1, 0), bold),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    return t


def render_pages(path: Path, title: str, pages: Sequence[Page]) -> None:
    """Každá položka `pages` je presne jedna strana PDF (explicitný PageBreak) —
    gold čísla strán v otázkach tak sedia z konštrukcie."""
    styles = _styles()
    flow: list[Flowable] = [Paragraph(title, styles["title"])]
    for i, page in enumerate(pages):
        if i:
            flow.append(PageBreak())
        for kind, content in page:
            if kind == "table":
                assert isinstance(content, list)
                flow += [Spacer(1, 0.3 * cm), _table(content), Spacer(1, 0.4 * cm)]
            else:
                assert isinstance(content, str)
                flow += [Paragraph(content, styles[kind]), Spacer(1, 0.25 * cm)]
    SimpleDocTemplate(str(path), pagesize=A4, title=title).build(flow)

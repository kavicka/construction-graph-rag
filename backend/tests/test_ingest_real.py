"""Integration test with a real Docling — slow (first run downloads ML models).

Runs only with RUN_DOCLING=1:  RUN_DOCLING=1 uv run pytest tests/test_ingest_real.py -v
"""

import os

import pytest

from app import db
from app.config import get_settings

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DOCLING") != "1", reason="pomalý integračný test — RUN_DOCLING=1"
)


def _make_sample_pdf(path: str) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4)
    data = [
        ["Vzorka", "Prietok (l/min)", "Teplota (°C)", "Tlak (kPa)"],
        ["A-01", "12.4", "21.5", "101.3"],
        ["A-02", "13.1", "22.0", "101.1"],
        ["B-01", "9.8", "20.9", "100.8"],
        ["B-02", "10.2", "21.2", "100.9"],
    ]
    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]
        )
    )
    doc.build(
        [
            Paragraph("Meranie prietoku chladiaceho média", styles["Title"]),
            Paragraph(
                "Tento dokument zhŕňa výsledky merania prietoku, teploty a tlaku "
                "chladiaceho média v testovacom okruhu. Hodnoty v tabuľke nižšie "
                "boli zaznamenané kalibrovaným snímačom.",
                styles["Normal"],
            ),
            Spacer(1, 1 * cm),
            table,
        ]
    )


def test_full_parse_with_table() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    db.init_db()

    doc_id = "integracny"
    _make_sample_pdf(str(settings.pdf_dir / f"{doc_id}.pdf"))
    db.insert_document(doc_id, "vzorka.pdf", "sha-integracny")

    from app.ingest.docling_parse import parse_document

    summary = parse_document(doc_id)

    assert summary["pages"] >= 1
    assert summary["tables"] >= 1
    assert (settings.parsed_dir / f"{doc_id}.json").exists()

    tables = db.list_tables(doc_id)
    assert tables, "tabuľka sa mala extrahovať"
    t = tables[0]
    assert t["n_rows"] >= 4
    assert t["n_cols"] == 4

    import pyarrow.parquet as pq

    assert t["parquet_path"], "parquet sa mal vytvoriť"
    arrow = pq.read_table(t["parquet_path"])
    assert arrow.num_rows >= 3

"""Presný filter nad ľubovoľnými tabuľkami — nielen jedálniček."""

import pytest

from app import db
from app.config import get_settings
from app.ingest.records import extract_records, header_attr, parse_numeric_cell
from app.retrieve.records_query import format_filter_answer, run_filter
from app.retrieve.router import route_query
from eval.construction import QUESTIONS

KONTROLA = """| Vzorka | Konštrukcia | Pevnosť (MPa) | Výsledok |
|---|---|---|---|
| K-01 | Základová doska | 41.2 | vyhovuje |
| K-03 | Strop nad 2. NP | 36.4 | nevyhovuje |
| K-05 | Steny 1. NP | 38.9 | vyhovuje |
"""
ROZPOCET_A = """| Č. | Stavebný objekt | Cena (EUR) |
|---|---|---|
| 1 | Zemné práce | 312 400 |
| 2 | Základy | 488 900 |
| 3 | Hrubá stavba | 1 742 300 |
"""
ROZPOCET_S = """| Č. | Stavebný objekt | Cena (EUR) |
|---|---|---|
| 1 | Zemné práce | 540 000 |
| 2 | Oceľová konštrukcia | 2 310 000 |
"""


def _table(doc_id: str, filename: str, md: str) -> None:
    settings = get_settings()
    if db.get_document(doc_id) is None:
        db.insert_document(doc_id, filename, f"sha-{doc_id}")
    path = settings.tables_dir / f"t-{doc_id}.md"
    path.write_text(md, encoding="utf-8")
    db.insert_table(f"t-{doc_id}", doc_id, 1, None, 4, 4, str(path), None)
    extract_records(doc_id)


@pytest.fixture
def library() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    _table("kk", "protokol-kontroly-kvality-agatova.pdf", KONTROLA)
    _table("ra", "rozpocet-agatova.pdf", ROZPOCET_A)
    _table("rs", "rozpocet-senec.pdf", ROZPOCET_S)


def test_numeric_cells_and_headers() -> None:
    assert parse_numeric_cell("1 742 300") == 1742300
    assert parse_numeric_cell("36,4") == 36.4
    assert parse_numeric_cell("12 %") == 12
    assert parse_numeric_cell("10. 9. 2026") is None
    assert parse_numeric_cell("5-50") is None
    assert header_attr("Pevnosť (MPa)") == ("pevnost", "Pevnosť", "MPa")
    assert header_attr("Max deviation (%)") == ("max_deviation", "Max deviation", "%")
    assert header_attr("Č.") is None
    assert header_attr("IČO") is None


def test_registry_from_headers(library: None) -> None:
    attrs = {a["key"]: (a["label"], a["unit"], a["docs"]) for a in db.list_record_attrs()}
    assert attrs["pevnost"] == ("Pevnosť", "MPa", 1)
    assert attrs["cena"] == ("Cena", "EUR", 2)
    assert "c" not in attrs  # poradové číslo nie je atribút


def test_filter_by_unit(library: None) -> None:
    plan = route_query("Ktoré vzorky majú pevnosť pod 38 MPa?")
    assert plan.mode == "filter"
    assert [(c.attr, c.op, c.value) for c in plan.constraints] == [("pevnost", "<=", 38.0)]
    matched, _ = run_filter(plan)
    assert [h.name for h in matched] == ["K-03"]


def test_filter_scoped_to_document(library: None) -> None:
    plan = route_query("Ktoré položky rozpočtu Agátová stoja viac ako 400 000 EUR?")
    assert plan.mode == "filter"
    assert [(c.attr, c.op, c.value) for c in plan.constraints] == [("cena", ">=", 400000.0)]
    assert "ra" in plan.doc_ids and "rs" not in plan.doc_ids
    matched, nearest = run_filter(plan)
    assert {h.name for h in matched} == {"Základy", "Hrubá stavba"}
    answer = format_filter_answer(plan, matched, nearest, [])
    assert "Cena [EUR] >= 400 000" in answer
    assert "Hrubá stavba" in answer and "Cena [EUR] 1 742 300" in answer


def test_filter_without_document_spans_library(library: None) -> None:
    matched, _ = run_filter(route_query("Ktoré stavebné objekty stoja nad 500 000 EUR?"))
    assert {h.name for h in matched} == {"Hrubá stavba", "Zemné práce", "Oceľová konštrukcia"}


def test_eval_questions_stay_on_rag_path(library: None) -> None:
    """Faktografické otázky z golden setu nesmú omylom spadnúť do filtra."""
    filtered = [q.id for q in QUESTIONS if route_query(q.question).mode == "filter"]
    assert filtered == []

"""Docling post-processing: heading → table association, page text lengths, table export and
parse_document orchestration. The converter (ML models) is replaced by a fake that returns a
real DoclingDocument built in code."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from docling_core.types.doc import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
    TableItem,
)

from app import db
from app.config import get_settings
from app.ingest import docling_parse
from app.ingest.docling_parse import (
    MAX_HEADING_PAGE_DISTANCE,
    _extract_tables,
    _headings_before_tables,
    _page_text_lengths,
    parse_document,
)

LONG = (
    "Toto je dlhší odsek bežného textu na strane, ktorý nie je nadpisom ani kartou kategórie. " * 2
)


def _prov(page: int) -> ProvenanceItem:
    return ProvenanceItem(
        page_no=page, bbox=BoundingBox(l=60, t=700, r=300, b=650), charspan=(0, 1)
    )


def _cell(text: str, row: int, col: int, header: bool = False) -> TableCell:
    return TableCell(
        text=text,
        start_row_offset_idx=row,
        end_row_offset_idx=row + 1,
        start_col_offset_idx=col,
        end_col_offset_idx=col + 1,
        column_header=header,
    )


def _new_doc(pages: int = 6) -> DoclingDocument:
    doc = DoclingDocument(name="jedalnicek")
    for p in range(1, pages + 1):
        doc.add_page(page_no=p, size=Size(width=600, height=800))
    return doc


def _add_table(doc: DoclingDocument, page: int, caption: str | None = None) -> TableItem:
    cap = (
        doc.add_text(label=DocItemLabel.CAPTION, text=caption, prov=_prov(page))
        if caption
        else None
    )
    return doc.add_table(
        data=TableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                _cell("Jedlo", 0, 0, True),
                _cell("kcal", 0, 1, True),
                _cell("Gulášovka", 1, 0),
                _cell("320", 1, 1),
            ],
        ),
        caption=cap,
        prov=_prov(page),
    )


def _heading(doc: DoclingDocument, text: str, page: int) -> None:
    doc.add_heading(text, level=1, prov=_prov(page))


def _para(doc: DoclingDocument, text: str, page: int) -> None:
    doc.add_text(label=DocItemLabel.TEXT, text=text, prov=_prov(page))


# --- _page_text_lengths ----------------------------------------------------------------


def test_page_text_lengths_sums_per_page_and_includes_empty_pages() -> None:
    doc = _new_doc(3)
    _para(doc, "abcde", 1)
    _para(doc, "xy", 1)
    _para(doc, "zzz", 3)
    assert _page_text_lengths(doc) == [(1, 7), (2, 0), (3, 3)]


def test_page_text_lengths_counts_provenance_of_unknown_page() -> None:
    doc = _new_doc(1)
    _para(doc, "abc", 5)  # page 5 has no PageItem but the text exists
    assert _page_text_lengths(doc) == [(1, 0), (5, 3)]


def test_page_text_lengths_empty_document() -> None:
    assert _page_text_lengths(DoclingDocument(name="x")) == []


# --- _headings_before_tables -----------------------------------------------------------


def test_section_header_names_the_following_table() -> None:
    doc = _new_doc()
    _heading(doc, "Polievky", 1)
    _para(doc, LONG, 1)
    table = _add_table(doc, 1)
    assert _headings_before_tables(doc) == {table.self_ref: "Polievky"}


def test_nearest_previous_heading_wins() -> None:
    doc = _new_doc()
    _heading(doc, "Polievky", 1)
    t1 = _add_table(doc, 1)
    _heading(doc, "Hlavné jedlá", 1)
    t2 = _add_table(doc, 1)
    assert _headings_before_tables(doc) == {t1.self_ref: "Polievky", t2.self_ref: "Hlavné jedlá"}


def test_heading_is_valid_only_within_page_distance() -> None:
    doc = _new_doc(8)
    _heading(doc, "Dezerty", 1)
    near = _add_table(doc, 1 + MAX_HEADING_PAGE_DISTANCE)
    far = _add_table(doc, 2 + MAX_HEADING_PAGE_DISTANCE)
    mapping = _headings_before_tables(doc)
    assert mapping[near.self_ref] == "Dezerty"
    assert far.self_ref not in mapping  # must not leak across the whole document


def test_table_before_any_heading_gets_nothing() -> None:
    doc = _new_doc()
    table = _add_table(doc, 1)
    _heading(doc, "Neskôr", 2)
    assert _headings_before_tables(doc) == {}
    assert table.self_ref not in _headings_before_tables(doc)


def test_heading_on_later_page_than_table_does_not_apply() -> None:
    doc = _new_doc()
    _heading(doc, "Strana 3", 3)
    table = _add_table(doc, 1)  # reading order says heading first, page number says otherwise
    assert table.self_ref not in _headings_before_tables(doc)


def test_card_page_short_text_acts_as_category_title() -> None:
    doc = _new_doc()
    _para(doc, "Predjedlá", 1)  # lone short plain text on the page = category card
    table = _add_table(doc, 1)
    assert _headings_before_tables(doc) == {table.self_ref: "Predjedlá"}


def test_card_title_over_45_chars_is_not_a_title() -> None:
    doc = _new_doc()
    _para(doc, "Veľmi dlhý názov kategórie, ktorý rozhodne nie je krátky titulok", 1)
    table = _add_table(doc, 1)
    assert _headings_before_tables(doc) == {}
    assert table.self_ref not in _headings_before_tables(doc)


def test_ordinary_busy_page_text_is_not_a_title() -> None:
    doc = _new_doc()
    for _ in range(3):
        _para(doc, "Krátky text", 1)  # three texts → not a card page
    table = _add_table(doc, 1)
    assert table.self_ref not in _headings_before_tables(doc)


def test_blank_heading_is_ignored() -> None:
    doc = _new_doc()
    _heading(doc, "Skutočný", 1)
    _heading(doc, "   ", 1)
    table = _add_table(doc, 1)
    assert _headings_before_tables(doc)[table.self_ref] == "Skutočný"


# --- _extract_tables -------------------------------------------------------------------


@pytest.fixture
def dirs() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "jedalnicek.pdf", "sha-1")


def test_extract_tables_writes_files_and_db_rows(dirs: None) -> None:
    doc = _new_doc()
    _add_table(doc, 2, caption="Tabuľka 1: Ceny")
    assert _extract_tables("d1", doc) == 1

    [row] = db.list_tables("d1")
    assert row["caption"] == "Tabuľka 1: Ceny"
    assert (row["page_no"], row["n_rows"], row["n_cols"]) == (2, 2, 2)
    assert "Gulášovka" in Path(row["markdown_path"]).read_text(encoding="utf-8")
    assert row["parquet_path"] and Path(row["parquet_path"]).exists()
    boxes = json.loads(row["boxes_json"])
    assert boxes and boxes[0][0] == 2.0 and all(0.0 <= v <= 1.0 for v in boxes[0][1:])


def test_extract_tables_falls_back_to_heading_for_caption(dirs: None) -> None:
    doc = _new_doc()
    _heading(doc, "Polievky", 1)
    _add_table(doc, 1)
    _extract_tables("d1", doc)
    assert db.list_tables("d1")[0]["caption"] == "Polievky"


def test_extract_tables_without_any_caption_stores_null(dirs: None) -> None:
    doc = _new_doc()
    _add_table(doc, 1)
    _extract_tables("d1", doc)
    assert db.list_tables("d1")[0]["caption"] is None


def test_extract_tables_parquet_failure_keeps_markdown(
    dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: TableItem, doc: Any = None, **kw: Any) -> None:
        raise ValueError("cannot build dataframe")

    monkeypatch.setattr(TableItem, "export_to_dataframe", broken)
    doc = _new_doc()
    _add_table(doc, 1)
    assert _extract_tables("d1", doc) == 1  # ingest must not fail on the bonus export
    [row] = db.list_tables("d1")
    assert row["parquet_path"] is None
    assert Path(row["markdown_path"]).exists()


def test_extract_tables_multiple_tables_get_distinct_ids(dirs: None) -> None:
    doc = _new_doc()
    _add_table(doc, 1)
    _add_table(doc, 2)
    assert _extract_tables("d1", doc) == 2
    ids = {t["id"] for t in db.list_tables("d1")}
    assert len(ids) == 2


def test_extract_tables_no_tables(dirs: None) -> None:
    assert _extract_tables("d1", _new_doc()) == 0
    assert db.list_tables("d1") == []


# --- parse_document --------------------------------------------------------------------


class FakeConverter:
    def __init__(self, document: DoclingDocument) -> None:
        self.document = document
        self.paths: list[Path] = []

    def convert(self, path: Path) -> SimpleNamespace:
        self.paths.append(path)
        return SimpleNamespace(document=self.document)


def test_parse_document_end_to_end(dirs: None, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _new_doc(2)
    _heading(doc, "Polievky", 1)
    _para(doc, "Gulášovka s chlebom podávaná s kyslou smotanou a čerstvým pečivom z pece", 1)
    _add_table(doc, 2)
    converter = FakeConverter(doc)
    monkeypatch.setattr(docling_parse, "_get_converter", lambda: converter)

    result = parse_document("d1")

    settings = get_settings()
    assert converter.paths == [settings.pdf_dir / "d1.pdf"]
    assert result == {"pages": 2, "tables": 1}
    parsed = json.loads((settings.parsed_dir / "d1.json").read_text(encoding="utf-8"))
    assert parsed["texts"][1]["text"].startswith("Gulášovka s chlebom")
    assert db.get_pages("d1") == [
        {"page_no": 1, "text_len": sum(len(t["text"]) for t in parsed["texts"])},
        {"page_no": 2, "text_len": 0},
    ]
    assert db.list_tables("d1")[0]["caption"] == "Polievky"  # heading from the previous page


def test_parse_document_uses_the_stored_format(dirs: None, monkeypatch: pytest.MonkeyPatch) -> None:
    db.insert_document("d2", "podklady.docx", "sha-2", fmt="docx")
    converter = FakeConverter(_new_doc(1))
    monkeypatch.setattr(docling_parse, "_get_converter", lambda: converter)
    parse_document("d2")
    assert converter.paths == [get_settings().pdf_dir / "d2.docx"]


def test_parse_document_unknown_id_defaults_to_pdf_then_fails_on_foreign_key(
    dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    converter = FakeConverter(_new_doc(1))
    monkeypatch.setattr(docling_parse, "_get_converter", lambda: converter)
    with pytest.raises(sqlite3.IntegrityError):  # pages need an existing document row
        parse_document("ghost")
    assert converter.paths == [get_settings().pdf_dir / "ghost.pdf"]  # missing row → "pdf"


def test_parse_document_replaces_tables_on_reparse(
    dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = _new_doc(1)
    _add_table(doc, 1)
    monkeypatch.setattr(docling_parse, "_get_converter", lambda: FakeConverter(doc))
    parse_document("d1")
    first_ids = {t["id"] for t in db.list_tables("d1")}
    parse_document("d1")
    second_ids = {t["id"] for t in db.list_tables("d1")}
    assert len(second_ids) == 1 and not (first_ids & second_ids)  # replaced, not duplicated


def test_parse_document_without_pages_falls_back_to_single_page(
    dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = DoclingDocument(name="prazdny")
    monkeypatch.setattr(docling_parse, "_get_converter", lambda: FakeConverter(empty))
    assert parse_document("d1") == {"pages": 1, "tables": 0}
    assert db.get_pages("d1") == [{"page_no": 1, "text_len": 0}]


def test_parse_document_converter_error_propagates(
    dirs: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Failing:
        def convert(self, path: Path) -> None:
            raise RuntimeError("corrupt pdf")

    monkeypatch.setattr(docling_parse, "_get_converter", lambda: Failing())
    with pytest.raises(RuntimeError, match="corrupt pdf"):
        parse_document("d1")
    assert not (get_settings().parsed_dir / "d1.json").exists()  # nothing half-written


# --- _get_converter --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("engine", "profile", "expect_ocr", "expect_mode"),
    [
        ("ocrmac", "low", True, "fast"),
        ("tesseract", "high", True, "accurate"),
        ("none", "low", False, "fast"),
    ],
)
def test_get_converter_options_follow_ocr_engine_and_profile(
    monkeypatch: pytest.MonkeyPatch, engine: str, profile: str, expect_ocr: bool, expect_mode: str
) -> None:
    captured: dict[str, Any] = {}

    class FakeConverterCls:
        def __init__(self, allowed_formats: list[Any], format_options: dict[Any, Any]) -> None:
            captured["formats"] = allowed_formats
            captured["options"] = next(iter(format_options.values())).pipeline_options

    from app.ocr import OcrInfo

    monkeypatch.setattr(
        docling_parse,
        "detect_ocr",
        lambda: OcrInfo(engine=engine, languages=["sk-SK"], slovak=True),
    )
    monkeypatch.setattr("docling.document_converter.DocumentConverter", FakeConverterCls)
    monkeypatch.setenv("RAG_RAM_PROFILE", profile)
    get_settings.cache_clear()
    docling_parse._get_converter.cache_clear()
    try:
        docling_parse._get_converter()
    finally:
        docling_parse._get_converter.cache_clear()

    opts = captured["options"]
    assert opts.do_ocr is expect_ocr
    assert opts.do_table_structure is True
    assert opts.table_structure_options.mode.value == expect_mode
    assert {f.value for f in captured["formats"]} == {"pdf", "docx", "pptx", "xlsx", "html", "md"}
    if engine == "tesseract":
        assert opts.ocr_options.lang == ["sk-SK"]

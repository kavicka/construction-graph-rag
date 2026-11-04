"""Chunking over a DoclingDocument: text chunks, skipped tables, contextual retrieval and
table-summary chunks. The HybridChunker itself (needs a tokenizer download) is replaced by a
fake that yields chunks over a real, programmatically built DoclingDocument."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from docling_core.types.doc import (
    BoundingBox,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)

from app import db
from app.config import get_settings
from app.index import chunker
from app.index.chunker import (
    CONTEXT_DOC_CHARS,
    build_table_chunks,
    chunk_text_items,
    contextualize_chunks,
    load_document,
)
from app.index.chunker import Chunk as TextChunk
from app.llm.ollama_client import OllamaError


def _prov(page: int) -> Any:
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


@pytest.fixture
def docling_doc() -> DoclingDocument:
    doc = DoclingDocument(name="zmluva")
    for page in (1, 2, 3):
        doc.add_page(page_no=page, size=Size(width=600, height=800))
    doc.add_heading("Zmluvné pokuty", level=1, prov=_prov(1))
    doc.add_text(label="text", text="Pokuta činí 0,05 % denne.", prov=_prov(1))  # type: ignore[arg-type]
    doc.add_text(label="text", text="Pokračovanie na ďalšej strane.", prov=_prov(2))  # type: ignore[arg-type]
    doc.add_table(
        data=TableData(
            num_rows=2,
            num_cols=2,
            table_cells=[
                _cell("Položka", 0, 0, True),
                _cell("Cena", 0, 1, True),
                _cell("Betón", 1, 0),
                _cell("120", 1, 1),
            ],
        ),
        prov=_prov(3),
    )
    return doc


def _fake_chunk(items: list[Any], headings: list[str] | None, text: str) -> SimpleNamespace:
    return SimpleNamespace(meta=SimpleNamespace(doc_items=items, headings=headings), text=text)


class FakeChunker:
    def __init__(self, chunks: list[SimpleNamespace]) -> None:
        self._chunks = chunks

    def chunk(self, dl_doc: Any) -> list[SimpleNamespace]:
        return self._chunks

    def contextualize(self, chunk: SimpleNamespace) -> str:
        heads = chunk.meta.headings or []
        return "\n".join([*heads, chunk.text]) if chunk.text.strip() else ""


def _use_chunker(monkeypatch: pytest.MonkeyPatch, chunks: list[SimpleNamespace]) -> None:
    monkeypatch.setattr(chunker, "_get_chunker", lambda: FakeChunker(chunks))


# --- chunk_text_items ------------------------------------------------------------------


def test_text_chunk_carries_context_pages_section_and_boxes(
    docling_doc: DoclingDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    heading, text1, text2 = docling_doc.texts
    _use_chunker(
        monkeypatch,
        [_fake_chunk([heading, text1, text2], ["Zmluva", "Zmluvné pokuty"], "Pokuta činí 0,05 %.")],
    )
    [chunk] = chunk_text_items("d1", docling_doc)
    assert chunk.chunk_id == "d1:t0"
    assert chunk.doc_id == "d1" and chunk.type == "text" and chunk.table_id is None
    assert chunk.text == "Zmluva\nZmluvné pokuty\nPokuta činí 0,05 %."  # embedded text has headings
    assert chunk.raw_text == "Pokuta činí 0,05 %."  # raw text stays clean for display
    assert chunk.section_path == "Zmluva / Zmluvné pokuty"
    assert (chunk.page_from, chunk.page_to) == (1, 2)
    assert {b[0] for b in chunk.boxes} == {1.0, 2.0}
    assert all(0.0 <= v <= 1.0 for b in chunk.boxes for v in b[1:])


def test_table_only_chunks_are_skipped_but_keep_numbering(
    docling_doc: DoclingDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = docling_doc.texts[1]
    table = docling_doc.tables[0]
    _use_chunker(
        monkeypatch,
        [
            _fake_chunk([text], None, "Prvý odsek."),
            _fake_chunk([table], None, "| Položka | Cena |"),  # goes through the summary branch
            _fake_chunk([text], None, "Tretí odsek."),
        ],
    )
    chunks = chunk_text_items("d1", docling_doc)
    assert [c.chunk_id for c in chunks] == ["d1:t0", "d1:t2"]  # ids follow the chunker index


def test_chunk_mixing_text_and_table_is_kept(
    docling_doc: DoclingDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_chunker(
        monkeypatch,
        [_fake_chunk([docling_doc.texts[1], docling_doc.tables[0]], None, "Text a tabuľka.")],
    )
    [chunk] = chunk_text_items("d1", docling_doc)
    assert (chunk.page_from, chunk.page_to) == (1, 3)


def test_blank_contextualized_chunk_is_dropped(
    docling_doc: DoclingDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_chunker(monkeypatch, [_fake_chunk([docling_doc.texts[1]], None, "   ")])
    assert chunk_text_items("d1", docling_doc) == []


def test_chunk_without_provenance_defaults_to_page_one(
    docling_doc: DoclingDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_prov = SimpleNamespace(prov=[])
    _use_chunker(monkeypatch, [_fake_chunk([no_prov], [], "Osirelý text.")])
    [chunk] = chunk_text_items("d1", docling_doc)
    assert (chunk.page_from, chunk.page_to) == (1, 1)
    assert chunk.section_path == ""
    assert chunk.boxes == []


def test_empty_document_yields_no_chunks(
    docling_doc: DoclingDocument, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_chunker(monkeypatch, [])
    assert chunk_text_items("d1", docling_doc) == []


# --- load_document / _get_chunker ------------------------------------------------------


def test_load_document_roundtrip(docling_doc: DoclingDocument) -> None:
    settings = get_settings()
    settings.ensure_dirs()
    (settings.parsed_dir / "d1.json").write_text(
        json.dumps(docling_doc.export_to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    loaded = load_document("d1")
    assert [t.text for t in loaded.texts] == [t.text for t in docling_doc.texts]
    assert "ďalšej" in loaded.texts[2].text  # diacritics survive the JSON roundtrip
    assert len(loaded.tables) == 1


def test_load_document_missing_file() -> None:
    get_settings().ensure_dirs()
    with pytest.raises(FileNotFoundError):
        load_document("neexistuje")


def test_get_chunker_uses_embedding_tokenizer_and_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class FakeHybrid:
        def __init__(self, tokenizer: Any, merge_peers: bool) -> None:
            seen["hybrid"] = (tokenizer, merge_peers)

    class FakeHFTokenizer:
        def __init__(self, tokenizer: Any, max_tokens: int) -> None:
            seen["hf"] = (tokenizer, max_tokens)

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(name: str) -> str:
            seen["model"] = name
            return f"tok:{name}"

    monkeypatch.setattr("docling_core.transforms.chunker.hybrid_chunker.HybridChunker", FakeHybrid)
    monkeypatch.setattr(
        "docling_core.transforms.chunker.tokenizer.huggingface.HuggingFaceTokenizer",
        FakeHFTokenizer,
    )
    monkeypatch.setattr("transformers.AutoTokenizer", FakeAutoTokenizer)
    chunker._get_chunker.cache_clear()
    try:
        first = chunker._get_chunker()
        assert chunker._get_chunker() is first
        settings = get_settings()
        assert seen["model"] == settings.effective_embedding_model
        assert seen["hf"] == (
            f"tok:{settings.effective_embedding_model}",
            settings.chunk_max_tokens,
        )
        assert seen["hybrid"][1] is True  # small neighbours are merged up to the token limit
    finally:
        chunker._get_chunker.cache_clear()


# --- contextualize_chunks --------------------------------------------------------------


def _chunk(cid: str, raw: str) -> TextChunk:
    return TextChunk(cid, "d1", raw, raw, 1, 1, "", "text", None)


class FakeDoc:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown

    def export_to_markdown(self) -> str:
        return self.markdown


@pytest.fixture
def ctx_db() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "Zmluva o dielo Agátová.pdf", "sha-1")


def test_contextualize_prepends_llm_context(ctx_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []

    def fake_generate(model: str, prompt: str, **kw: Any) -> str:
        prompts.append(prompt)
        assert kw["temperature"] == 0.0 and model == get_settings().answer_model
        return "  Zmluva o dielo Agátová, článok o pokutách.  "

    monkeypatch.setattr(chunker, "generate_sync", fake_generate)
    chunks = [_chunk("d1:t0", "Pokuta činí 0,05 %."), _chunk("d1:t1", "Záruka 60 mesiacov.")]
    assert contextualize_chunks("d1", FakeDoc("# Zmluva"), chunks) == 2
    assert chunks[0].text == "Zmluva o dielo Agátová, článok o pokutách.\n\nPokuta činí 0,05 %."
    assert chunks[0].raw_text == "Pokuta činí 0,05 %."  # raw text is untouched
    assert "Súbor: Zmluva o dielo Agátová.pdf" in prompts[0]
    assert "Pokuta činí 0,05 %." in prompts[0]


def test_contextualize_results_are_cached_across_runs(
    ctx_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_generate(model: str, prompt: str, **kw: Any) -> str:
        calls.append(prompt)
        return "Kontext."

    monkeypatch.setattr(chunker, "generate_sync", fake_generate)
    doc = FakeDoc("# Zmluva")
    contextualize_chunks("d1", doc, [_chunk("d1:t0", "Text A")])
    again = [_chunk("d1:t0", "Text A")]
    assert contextualize_chunks("d1", doc, again) == 1
    assert len(calls) == 1  # reindexing is free
    assert again[0].text == "Kontext.\n\nText A"
    # a different chunk text or a different document head is a different cache key
    contextualize_chunks("d1", doc, [_chunk("d1:t1", "Text B")])
    contextualize_chunks("d1", FakeDoc("# Iný obsah"), [_chunk("d1:t0", "Text A")])
    assert len(calls) == 3


def test_contextualize_llm_failure_leaves_chunk_untouched(
    ctx_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(model: str, prompt: str, **kw: Any) -> str:
        if "padne" in prompt:
            raise OllamaError("down")
        return "Kontext."

    monkeypatch.setattr(chunker, "generate_sync", flaky)
    chunks = [_chunk("d1:t0", "tento padne"), _chunk("d1:t1", "tento prejde")]
    assert contextualize_chunks("d1", FakeDoc("# D"), chunks) == 1
    assert chunks[0].text == "tento padne"  # ingest continues without context
    assert chunks[1].text.startswith("Kontext.")
    # a failure is not cached: the next run tries the LLM again
    monkeypatch.setattr(chunker, "generate_sync", lambda *a, **k: "Už to ide.")
    retry = [_chunk("d1:t0", "tento padne")]
    assert contextualize_chunks("d1", FakeDoc("# D"), retry) == 1


def test_contextualize_empty_context_is_not_prepended(
    ctx_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chunker, "generate_sync", lambda *a, **k: "   ")
    chunks = [_chunk("d1:t0", "Text")]
    assert contextualize_chunks("d1", FakeDoc("# D"), chunks) == 0
    assert chunks[0].text == "Text"


def test_contextualize_truncates_long_inputs(ctx_db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(chunker, "generate_sync", lambda m, p, **k: prompts.append(p) or "Kontext.")
    contextualize_chunks(
        "d1", FakeDoc("D" * (CONTEXT_DOC_CHARS * 2)), [_chunk("d1:t0", "C" * 5000)]
    )
    assert prompts[0].count("D") <= CONTEXT_DOC_CHARS
    assert prompts[0].count("C") == 2000


def test_contextualize_unknown_document_uses_id_as_title(
    ctx_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(chunker, "generate_sync", lambda m, p, **k: prompts.append(p) or "K.")
    contextualize_chunks("ghost", FakeDoc("# D"), [_chunk("ghost:t0", "Text")])
    assert "Súbor: ghost\n" in prompts[0]


def test_contextualize_no_chunks_makes_no_llm_calls(
    ctx_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> str:
        raise AssertionError("no LLM call expected")

    monkeypatch.setattr(chunker, "generate_sync", boom)
    assert contextualize_chunks("d1", FakeDoc("# D"), []) == 0


# --- build_table_chunks ----------------------------------------------------------------


def test_build_table_chunks_summarises_and_persists(
    ctx_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = tmp_path / "t1.md"
    md.write_text("| Položka | Cena |\n|---|---|\n| Betón | 120 |", encoding="utf-8")
    boxes = [[3.0, 0.1, 0.2, 0.5, 0.4]]
    db.insert_table("t1", "d1", 3, "Cenník", 2, 2, str(md), None, boxes_json=json.dumps(boxes))
    monkeypatch.setattr(chunker, "generate_sync", lambda *a, **k: "Tabuľka cien betónu.")

    [chunk] = build_table_chunks("d1")
    assert chunk.chunk_id == "d1:table:t1"
    assert chunk.type == "table_summary" and chunk.table_id == "t1"
    assert (chunk.page_from, chunk.page_to) == (3, 3)
    assert chunk.section_path == "Cenník"
    assert chunk.raw_text == "Tabuľka cien betónu."
    assert chunk.text == "Cenník\nTabuľka cien betónu.\nStĺpce: Položka | Cena"
    assert chunk.boxes == boxes
    stored = db.get_table("t1")
    assert stored is not None and stored["summary"] == "Tabuľka cien betónu."


def test_build_table_chunks_without_caption_markdown_or_page(
    ctx_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.insert_table("t2", "d1", None, None, 4, 3, str(tmp_path / "missing.md"), None)

    def down(*a: Any, **k: Any) -> str:
        raise OllamaError("down")

    monkeypatch.setattr(chunker, "generate_sync", down)
    [chunk] = build_table_chunks("d1")
    assert (chunk.page_from, chunk.page_to) == (1, 1)
    assert chunk.section_path == ""
    assert chunk.boxes == []
    assert "Tabuľka 4×3" in chunk.raw_text  # deterministic fallback summary
    assert chunk.text.startswith("Tabuľka 4×3")  # no caption line, no empty lines
    assert "\n\n" not in chunk.text


def test_build_table_chunks_document_without_tables() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    assert build_table_chunks("nothing") == []


def test_build_table_chunks_orders_by_page(
    ctx_db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for tid, page in (("late", 9), ("early", 2)):
        path = tmp_path / f"{tid}.md"
        path.write_text("| A |\n|---|\n| 1 |", encoding="utf-8")
        db.insert_table(tid, "d1", page, tid, 1, 1, str(path), None)
    monkeypatch.setattr(chunker, "generate_sync", lambda *a, **k: "Súhrn.")
    assert [c.table_id for c in build_table_chunks("d1")] == ["early", "late"]

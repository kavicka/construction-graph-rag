from pathlib import Path

import pytest

from app import db
from app.company import CompanySettings
from app.config import get_settings
from app.docgen import generate
from app.docgen.generate import (
    Outline,
    build_content,
    clean_section,
    plan_outline,
    save_document,
    slug_filename,
    summary_message,
    write_section,
)
from app.docgen.intent import detect_generate_intent
from app.docgen.pdf import _make_fetcher, render_pdf
from app.docgen.render import _page_counter_css, build_html, markdown_to_html
from app.docgen.schema import OUTLINES
from app.docgen.theme import FONTS_DIR, THEME
from app.llm.ollama_client import OllamaError
from app.retrieve.pipeline import Source
from app.retrieve.router import route_query


def _src(n: int, filename: str = "zmluva.pdf") -> Source:
    return Source(
        n=n,
        chunk_id=f"c{n}",
        doc_id="d1",
        filename=filename,
        page_from=n,
        page_to=n,
        type="text",
        table_id=None,
        text=f"Zdroj {n}: cena diela 12 400 EUR.",
        snippet=f"snippet {n}",
    )


@pytest.fixture
def ready_db() -> None:
    get_settings().ensure_dirs()
    db.init_db()


# --- intent ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "doc_type", "lang"),
    [
        ("Vytvor cenovú ponuku na rekonštrukciu strechy", "offer", "sk"),
        ("Priprav mi report o meraní prietoku", "report", "sk"),
        ("sprav súhrn všetkých zmlúv", "summary", "sk"),
        ("Napíš interné memo o termínoch", "memo", "sk"),
        ("Vygeneruj dokument o skladovaní", "free", "sk"),
        ("zhrň mi kalibráciu do PDF", "summary", "sk"),
        ("Create a report on the calibration results", "report", "en"),
        ("Draft a proposal for the client", "offer", "en"),
        ("export the storage rules as a PDF", "free", "en"),
    ],
)
def test_intent_detected(question: str, doc_type: str, lang: str) -> None:
    intent = detect_generate_intent(question)
    assert intent is not None, question
    assert intent.doc_type == doc_type
    assert intent.lang == lang


@pytest.mark.parametrize(
    "question",
    [
        "Aký bol prietok pri meraní?",
        "Čo hovorí dokument o skladovaní?",  # document without a creation verb
        "Vytvor zoznam položiek do 500 kcal",  # verb without a document type
        "zhrň mi kalibráciu",  # no PDF → ordinary chat answer
        "Which document mentions calibration?",
    ],
)
def test_intent_not_detected(question: str) -> None:
    assert detect_generate_intent(question) is None


def test_router_prefers_generate(ready_db: None) -> None:
    plan = route_query("Priprav ponuku na obed do 870 kcal")
    assert plan.mode == "generate"
    assert plan.doc_type == "offer"


# --- render ---------------------------------------------------------------


def test_markdown_escapes_raw_html_and_links_citations() -> None:
    out = markdown_to_html("Text [1] a [2, 9].\n\n<script>alert(1)</script>", {1, 2})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out
    assert '<a href="#src-1">1</a>' in out
    assert '<a href="#src-2">2</a>' in out
    assert "src-9" not in out  # a non-existent source is dropped


def test_markdown_drops_fully_invalid_citation() -> None:
    assert "[7]" not in markdown_to_html("Veta [7].", {1})


def test_page_counter_css() -> None:
    assert _page_counter_css("Strana {page} z {pages}") == (
        '"Strana " counter(page) " z " counter(pages)'
    )


def test_build_html_uses_app_theme(ready_db: None) -> None:
    company = CompanySettings(
        name="Firma <b>s.r.o.</b>",
        ico="12345678",
        footer_text="OR OS Žilina",
    )
    content = build_content(
        Outline("Názov", "Podnadpis", ["A", "B"]), "report", "sk", ["Prvá [1].", "Druhá."]
    )
    out = build_html(content, company, [_src(1), _src(2)])
    # app theme: orange accent, system-ui text, JetBrains Mono labels
    assert THEME.primary in out and THEME.foreground in out
    assert "system-ui" in out
    assert "jetbrains-mono-400-latin.woff2" in out and "jetbrains-mono-700-ext.woff2" in out
    assert "Firma &lt;b&gt;s.r.o.&lt;/b&gt;" in out  # autoescape
    assert "IČO: 12345678" in out
    # only source 1 is cited → the sources list shows only that one
    assert 'id="src-1"' in out
    assert 'id="src-2"' not in out


def test_render_pdf_produces_pages(tmp_path: Path) -> None:
    html = "<html><body>" + "<p>Odsek ťžčľ.</p>" * 400 + "</body></html>"
    rendered = render_pdf(html, allowed_dirs=[tmp_path])
    assert rendered.data.startswith(b"%PDF")
    assert rendered.pages >= 2


def test_fetcher_sandbox(tmp_path: Path) -> None:
    allowed = tmp_path / "branding"
    allowed.mkdir()
    (allowed / "logo.png").write_bytes(b"x")
    fetcher = _make_fetcher([allowed])
    for url in (
        "http://example.com/x.png",
        "https://example.com/x.png",
        "file:///etc/hosts",
        (tmp_path / "secret.txt").as_uri(),
        (allowed / ".." / "secret.txt").as_uri(),
    ):
        with pytest.raises(ValueError):
            fetcher.fetch(url)
    assert fetcher.fetch((allowed / "logo.png").as_uri()) is not None
    font = _make_fetcher([FONTS_DIR]).fetch((FONTS_DIR / "jetbrains-mono-400-latin.woff2").as_uri())
    assert font is not None


def test_render_ignores_blocked_resources(tmp_path: Path) -> None:
    html = '<p>ok</p><img src="file:///etc/hosts"><img src="http://127.0.0.1:1/x.png">'
    assert render_pdf(html, allowed_dirs=[tmp_path]).data.startswith(b"%PDF")


# --- generate -------------------------------------------------------------


def test_outline_uses_llm_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        generate,
        "generate_sync",
        lambda *a, **k: (
            '{"title": "Prehľad zmlúv 2027", "subtitle": "Kľúčové termíny",'
            ' "sections": ["Úvod", "Termíny", "Riziká"]}'
        ),
    )
    free = plan_outline("vygeneruj dokument", "free", "sk", [_src(1)])
    assert free.title == "Prehľad zmlúv 2027"
    assert free.headings == ["Úvod", "Termíny", "Riziká"]
    # the template has a fixed outline, the LLM supplies only the title
    report = plan_outline("priprav report", "report", "sk", [_src(1)])
    assert report.headings == OUTLINES["sk"]["report"]


@pytest.mark.parametrize(
    "raw", ["nie json", '{"title": "x"}', '{"title": "OK title", "sections": ["jediná"]}']
)
def test_outline_fallbacks(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setattr(generate, "generate_sync", lambda *a, **k: raw)
    outline = plan_outline("vygeneruj dokument o meraní", "free", "sk", [])
    assert len(outline.headings) >= 2
    assert outline.title


def test_outline_ollama_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> str:
        raise OllamaError("down")

    monkeypatch.setattr(generate, "generate_sync", boom)
    outline = plan_outline("priprav ponuku pre obec", "offer", "sk", [])
    assert outline.title == "Priprav ponuku pre obec"
    assert outline.headings == OUTLINES["sk"]["offer"]
    body = write_section("q", "Úvod", outline, "offer", "sk", [], CompanySettings())
    assert "Podklady" in body


def test_clean_section() -> None:
    assert clean_section("## Úvod\nText [1].", "Úvod") == "Text [1]."
    assert clean_section("**Úvod**\nText.", "Úvod") == "Text."
    assert clean_section("<think>hm</think>\nText.", "X") == "Text."
    assert clean_section("Text bez nadpisu.", "Úvod") == "Text bez nadpisu."


def test_slug_filename() -> None:
    assert slug_filename("Cenová ponuka – Ľubochňa 2027!") == "cenova-ponuka-lubochna-2027.pdf"
    assert slug_filename("!!!") == "dokument.pdf"


def test_save_document_persists(ready_db: None) -> None:
    content = build_content(Outline("Report Ťž", "", ["A", "B"]), "report", "sk", ["X [1].", "Y."])
    doc = save_document(content, CompanySettings(name="Firma"), [_src(1)], None)
    assert (get_settings().generated_dir / f"{doc.id}.pdf").read_bytes().startswith(b"%PDF")
    row = db.get_generated_document(doc.id)
    assert row is not None
    assert row["pages"] == doc.pages >= 1
    assert doc.to_json()["url"] == f"/api/generated/{doc.id}/pdf"
    assert "1 strana" in summary_message(doc, "sk", 1)

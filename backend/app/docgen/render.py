"""DocumentContent + nastavenia firmy → branded HTML → PDF."""

import html
import re
from datetime import date
from pathlib import Path

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.company import CompanySettings
from app.docgen.pdf import RenderedPdf, render_pdf
from app.docgen.schema import DOC_TYPE_LABELS, UI_TEXT, DocumentContent
from app.docgen.theme import THEME
from app.retrieve.pipeline import Source

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def _cite_links(body_html: str, valid: set[int]) -> str:
    """[1] / [1, 3] → horný index s odkazom na zoznam zdrojov; neplatné čísla zahodí."""

    def repl(m: re.Match[str]) -> str:
        nums = [int(x) for x in m.group(1).split(",") if int(x) in valid]
        if not nums:
            return ""
        links = ",".join(f'<a href="#src-{n}">{n}</a>' for n in nums)
        return f'<sup class="cite">[{links}]</sup>'

    return CITE_RE.sub(repl, body_html)


def markdown_to_html(body_md: str, valid_citations: set[int]) -> str:
    # surové HTML od LLM sa escapuje — do PDF ide len to, čo vyrobí markdown
    escaped = html.escape(body_md, quote=False)
    rendered = markdown.markdown(escaped, extensions=["tables", "sane_lists"])
    return _cite_links(rendered, valid_citations)


def _page_counter_css(template: str) -> str:
    """'Strana {page} z {pages}' → CSS content s countermi."""
    parts = re.split(r"(\{page\}|\{pages\})", template)
    css = []
    for part in parts:
        if part == "{page}":
            css.append("counter(page)")
        elif part == "{pages}":
            css.append("counter(pages)")
        elif part:
            css.append('"' + part.replace('"', '\\"') + '"')
    return " ".join(css)


def build_html(
    content: DocumentContent,
    company: CompanySettings,
    sources: list[Source],
    *,
    today: date | None = None,
) -> str:
    text = UI_TEXT[content.lang]
    valid = {s.n for s in sources}
    sections = [
        {"heading": s.heading, "html": markdown_to_html(s.body_md, valid)} for s in content.sections
    ]
    # v zozname zdrojov len tie, ktoré dokument naozaj cituje (inak všetky)
    cited = {
        int(n)
        for s in content.sections
        for m in CITE_RE.finditer(s.body_md)
        for n in m.group(1).split(",")
    }
    shown_sources = [s for s in sources if s.n in cited] or sources
    day = today or date.today()
    return _env.get_template("document.html.j2").render(
        lang=content.lang,
        title=content.title,
        subtitle=content.subtitle,
        doc_type=content.doc_type,
        doc_type_label=DOC_TYPE_LABELS[content.lang][content.doc_type],
        sections=sections,
        sources=shown_sources,
        company=company,
        theme=THEME,
        text=text,
        date=day.strftime("%d. %m. %Y") if content.lang == "sk" else day.isoformat(),
        page_counter_css=_page_counter_css(text["page_of"]),
    )


def render_document(
    content: DocumentContent, company: CompanySettings, sources: list[Source]
) -> RenderedPdf:
    return render_pdf(build_html(content, company, sources), allowed_dirs=[])

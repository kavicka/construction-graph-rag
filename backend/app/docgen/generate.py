"""Document generation: outline (JSON) → sections one at a time (markdown) → PDF.

A small local model (qwen3:4b) cannot reliably write a whole document as one JSON,
so it works in steps: a short structured output for the outline, then free
markdown for each section separately. Every step has a fallback — a document is
always produced, even if the LLM returns nonsense.
"""

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app import db
from app.company import CompanySettings
from app.config import get_settings
from app.docgen.render import render_document
from app.docgen.schema import (
    DOC_TYPE_LABELS,
    OUTLINES,
    DocSection,
    DocType,
    DocumentContent,
    Lang,
)
from app.llm.ollama_client import OllamaError, generate_sync
from app.retrieve.pipeline import DATA_RULE, Source, format_source


def settings_guard() -> bool:
    return get_settings().injection_guard


logger = logging.getLogger(__name__)

MIN_SECTIONS, MAX_SECTIONS = 2, 6
FALLBACK_HEADINGS: dict[Lang, list[str]] = {
    "sk": ["Prehľad", "Podrobnosti", "Záver"],
    "en": ["Overview", "Details", "Conclusion"],
}


class _Outline(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    subtitle: str = Field(default="", max_length=200)
    sections: list[str] = Field(default_factory=list, max_length=MAX_SECTIONS)


@dataclass
class Outline:
    title: str
    subtitle: str
    headings: list[str]


@dataclass
class GeneratedDocument:
    id: str
    title: str
    doc_type: DocType
    pages: int
    filename: str
    sections: list[str]

    @property
    def url(self) -> str:
        return f"/api/generated/{self.id}/pdf"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "doc_type": self.doc_type,
            "pages": self.pages,
            "filename": self.filename,
            "sections": self.sections,
            "url": self.url,
        }


def _sources_block(sources: list[Source]) -> str:
    if not sources:
        return "(žiadne zdroje sa nenašli)"
    return "\n\n".join(format_source(s) for s in sources)


def _fallback_title(question: str) -> str:
    title = re.sub(r"\s+", " ", question).strip().rstrip("?.!")
    return (title[:1].upper() + title[1:])[:120] or "Dokument"


def plan_outline(question: str, doc_type: DocType, lang: Lang, sources: list[Source]) -> Outline:
    fixed = OUTLINES[lang][doc_type]
    label = DOC_TYPE_LABELS[lang][doc_type]
    titles_only = "\n".join(f"[{s.n}] {s.filename}: {s.snippet[:160]}" for s in sources[:10])
    if lang == "sk":
        prompt = (
            f"Navrhni osnovu dokumentu typu „{label}“ podľa zadania používateľa.\n"
            f"Zadanie: {question}\n\nDostupné podklady:\n{titles_only}\n\n"
            "Vráť JSON: title = výstižný názov dokumentu (nie zadanie doslova), "
            "subtitle = jedna veta o čom dokument je, "
            f"sections = {MIN_SECTIONS}–{MAX_SECTIONS} nadpisov sekcií."
        )
    else:
        prompt = (
            f"Propose an outline for a document of type “{label}” for this request.\n"
            f"Request: {question}\n\nAvailable material:\n{titles_only}\n\n"
            "Return JSON: title = concise document title (not the request verbatim), "
            "subtitle = one sentence on what it covers, "
            f"sections = {MIN_SECTIONS}–{MAX_SECTIONS} section headings."
        )
    try:
        raw = generate_sync(
            get_settings().answer_model,
            prompt,
            json_schema=_Outline.model_json_schema(),
            num_ctx=4096,
            timeout=180,
        )
        parsed = _Outline.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("osnova dokumentu zlyhala: %s", exc)
        return Outline(
            title=_fallback_title(question), subtitle="", headings=fixed or FALLBACK_HEADINGS[lang]
        )

    headings = fixed or [h.strip() for h in parsed.sections if h.strip()][:MAX_SECTIONS]
    if len(headings) < MIN_SECTIONS:
        headings = FALLBACK_HEADINGS[lang]
    return Outline(title=parsed.title.strip(), subtitle=parsed.subtitle.strip(), headings=headings)


SECTION_SYSTEM: dict[Lang, str] = {
    "sk": """Píšeš jednu sekciu firemného dokumentu. Pravidlá:
- Píš VÝHRADNE z poskytnutých zdrojov. Za každé tvrdenie pridaj citáciu [n].
- Ak zdroje k téme sekcie nič neobsahujú, napíš jednu vetu, že podklady to neuvádzajú.
- Čísla uvádzaj presne ako v zdroji — nezaokrúhľuj, nedopočítavaj.
- Formát: markdown (odseky, odrážky, prípadne tabuľka). NEPÍŠ nadpis sekcie.
- Rozsah 60–220 slov. Vecný, profesionálny tón. Píš po slovensky.""",
    "en": """You write one section of a business document. Rules:
- Use ONLY the provided sources. Cite every claim as [n].
- If the sources say nothing about the section topic, write one sentence saying so.
- Quote numbers exactly as in the source — no rounding, no derived figures.
- Format: markdown (paragraphs, bullets, a table if useful). Do NOT write the heading.
- 60–220 words. Factual, professional tone. Write in English.""",
}

_HEADING_LINE = re.compile(r"^\s*#{1,6}\s+.*$\n?", re.MULTILINE)
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


def clean_section(body: str, heading: str) -> str:
    body = _THINK.sub("", body).strip()
    # the model sometimes repeats the heading — either as a markdown heading or a bold first line
    body = _HEADING_LINE.sub("", body, count=1) if body.lstrip().startswith("#") else body
    first, _, rest = body.partition("\n")
    if first.strip("*_ :").casefold() == heading.casefold():
        body = rest
    return body.strip()


def write_section(
    question: str,
    heading: str,
    outline: Outline,
    doc_type: DocType,
    lang: Lang,
    sources: list[Source],
    company: CompanySettings,
) -> str:
    label = DOC_TYPE_LABELS[lang][doc_type]
    others = ", ".join(h for h in outline.headings if h != heading)
    if lang == "sk":
        user = (
            f"Dokument: {label} „{outline.title}“"
            + (f" pre firmu {company.name}" if company.name else "")
            + f"\nZadanie používateľa: {question}\n"
            f"Napíš sekciu: {heading}\n(Ostatné sekcie, neopakuj ich obsah: {others})\n\n"
            f"Zdroje:\n{_sources_block(sources)}"
        )
    else:
        user = (
            f"Document: {label} “{outline.title}”"
            + (f" for {company.name}" if company.name else "")
            + f"\nUser request: {question}\n"
            f"Write the section: {heading}\n(Other sections, do not repeat them: {others})\n\n"
            f"Sources:\n{_sources_block(sources)}"
        )
    try:
        body = generate_sync(
            get_settings().answer_model,
            user,
            system=SECTION_SYSTEM[lang] + ("\n" + DATA_RULE if settings_guard() else ""),
            temperature=0.3,
            num_ctx=8192,
            timeout=300,
            max_tokens=2048,
        )
    except OllamaError as exc:
        logger.warning("sekcia %r zlyhala: %s", heading, exc)
        body = ""
    cleaned = clean_section(body, heading)
    if not cleaned:
        cleaned = (
            "Podklady k tejto časti neobsahujú dostatok informácií."
            if lang == "sk"
            else "The source material does not cover this part."
        )
    return cleaned


def slug_filename(title: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().casefold()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:60].rstrip("-")
    return f"{slug or 'dokument'}.pdf"


def save_document(
    content: DocumentContent,
    company: CompanySettings,
    sources: list[Source],
    chat_id: str | None,
) -> GeneratedDocument:
    rendered = render_document(content, company, sources)
    doc_id = uuid.uuid4().hex
    settings = get_settings()
    settings.generated_dir.mkdir(parents=True, exist_ok=True)
    (settings.generated_dir / f"{doc_id}.pdf").write_bytes(rendered.data)
    from app.auth import current_scope

    db.insert_generated_document(
        doc_id,
        chat_id,
        content.title,
        content.doc_type,
        rendered.pages,
        json.dumps([s.to_json() for s in sources], ensure_ascii=False),
        current_scope().user_id,
    )
    return GeneratedDocument(
        id=doc_id,
        title=content.title,
        doc_type=content.doc_type,
        pages=rendered.pages,
        filename=slug_filename(content.title),
        sections=[s.heading for s in content.sections],
    )


def summary_message(doc: GeneratedDocument, lang: Lang, cited: int) -> str:
    sections = ", ".join(doc.sections)
    if lang == "sk":
        pages = "strana" if doc.pages == 1 else "strany" if doc.pages < 5 else "strán"
        return (
            f"Vytvoril som dokument **{doc.title}** ({doc.pages} {pages}). "
            f"Sekcie: {sections}. Citovaných zdrojov: {cited}."
        )
    return (
        f"Created **{doc.title}** ({doc.pages} page{'s' if doc.pages != 1 else ''}). "
        f"Sections: {sections}. Sources cited: {cited}."
    )


def build_content(
    outline: Outline, doc_type: DocType, lang: Lang, bodies: list[str]
) -> DocumentContent:
    return DocumentContent(
        doc_type=doc_type,
        lang=lang,
        title=outline.title,
        subtitle=outline.subtitle,
        sections=[DocSection(h, b) for h, b in zip(outline.headings, bodies, strict=True)],
    )


def export_answer_pdf(
    answer_md: str, sources_json: list[dict[str, Any]], question: str, chat_id: str | None
) -> dict[str, Any]:
    """Save an existing answer (e.g. deep research) as a branded report."""
    from app.company import load_company
    from app.retrieve.research import sections_from_markdown

    lang: Lang = "en" if re.search(r"\b(the|what|which|how)\b", question, re.I) else "sk"
    sections = sections_from_markdown(answer_md) or [
        ("Odpoveď" if lang == "sk" else "Answer", answer_md)
    ]
    sources = [
        Source(
            n=s["n"],
            chunk_id=s["chunk_id"],
            doc_id=s["doc_id"],
            filename=s["filename"],
            page_from=s["page_from"],
            page_to=s["page_to"],
            type=s["type"],
            table_id=s.get("table_id"),
            text="",
            snippet=s.get("snippet", ""),
        )
        for s in sources_json
    ]
    content = DocumentContent(
        doc_type="report",
        lang=lang,
        title=_fallback_title(question),
        sections=[DocSection(h, b) for h, b in sections],
    )
    return save_document(content, load_company(), sources, chat_id).to_json()

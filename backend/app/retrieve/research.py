"""Question decomposition and deep research.

1) Compound question („Kto je stavbyvedúci a aká je pokuta?“) → sub-questions → retrieval for
   each separately → merged sources. A single search would find only one half.
2) Deep research: sub-question plan → parallel retrieval → a short finding per
   sub-question → synthesis into a structured report with citations (exportable to PDF).
"""

import logging
import re
from dataclasses import replace
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync
from app.retrieve.pipeline import DATA_RULE, Source, format_source, retrieve_sources

logger = logging.getLogger(__name__)

MAX_SUBQUESTIONS = 5
MAX_MERGED_SOURCES = 14
COMPOUND = re.compile(
    r"\s(?:a|aj|and|ako aj)\s+(?:tiež\s+|also\s+)?"
    r"(?:aký|aká|aké|akú|akých|kto|koho|kedy|koľko|kde|ako|prečo|čo|ktor\w+|"
    r"what|who|when|how|where|which|why)\b",
    re.IGNORECASE,
)
RESEARCH = re.compile(
    r"^\s*/research\b|\bdeep research\b|\bpreskúmaj\b|\brešerš\w*\b|\bdôkladn\w+ analýz\w*\b|"
    r"\bzanalyzuj všetk\w*\b|\binvestigate\b|\bresearch everything\b",
    re.IGNORECASE,
)


class _Plan(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=MAX_SUBQUESTIONS)


def is_compound(question: str) -> bool:
    return question.count("?") >= 2 or bool(COMPOUND.search(question))


def is_research_request(question: str) -> bool:
    return bool(RESEARCH.search(question))


CONJUNCTION = re.compile(r"\s(?:a|aj|and|ako aj)\s+(?:tiež\s+|also\s+)?", re.IGNORECASE)


def _split_heuristic(question: str) -> list[str]:
    """Splits on question marks and before a conjunction + interrogative („… a aká je …“)."""
    pieces: list[str] = []
    for segment in re.split(r"\?\s*", question):
        last = 0
        for m in COMPOUND.finditer(segment):
            pieces.append(segment[last : m.start()])
            conj = CONJUNCTION.match(segment, m.start())
            last = conj.end() if conj else m.start()
        pieces.append(segment[last:])
    out = [p.strip(" ,;") for p in pieces]
    return [p[0].upper() + p[1:] + "?" for p in out if len(p) > 8][:MAX_SUBQUESTIONS]


def decompose(question: str, *, research: bool = False) -> list[str]:
    """→ standalone sub-questions. Deep research also expands the topic; otherwise it splits."""
    if research:
        instruction = (
            "Navrhni 3 až 5 samostatných podotázok, ktoré treba zodpovedať z firemných "
            "dokumentov, aby vznikla dôkladná odpoveď na tému nižšie (fakty, čísla, termíny, "
            "súvislosti medzi dokumentmi, riziká). Každá podotázka musí dávať zmysel sama "
            "osebe — opakuj v nej názov projektu/subjektu."
        )
    else:
        instruction = (
            "Rozdeľ otázku na samostatné podotázky — jednu za každú vec, na ktorú sa pýta. "
            "Každá podotázka musí dávať zmysel sama osebe (zopakuj v nej projekt/subjekt). "
            "Nič nové nepridávaj."
        )
    prompt = f"{instruction}\nZachovaj jazyk otázky.\n\nOtázka: {question}"
    try:
        raw = generate_sync(
            get_settings().answer_model,
            prompt,
            json_schema=_Plan.model_json_schema(),
            temperature=0.1,
            num_ctx=4096,
            timeout=90,
        )
        questions = [q.strip() for q in _Plan.model_validate_json(raw).questions if q.strip()]
    except (OllamaError, ValidationError) as exc:
        logger.warning("rozklad otázky zlyhal: %s", exc)
        questions = []
    if len(questions) < 2 and not research:
        questions = _split_heuristic(question)
    return questions[:MAX_SUBQUESTIONS] or [question]


def merge_sources(per_query: list[list[Source]], *, cap: int = MAX_MERGED_SOURCES) -> list[Source]:
    """Round-robin over sub-questions — each gets its best sources into the context."""
    merged: list[Source] = []
    seen: set[str] = set()
    depth = max((len(s) for s in per_query), default=0)
    for rank in range(depth):
        for sources in per_query:
            if rank < len(sources) and sources[rank].chunk_id not in seen:
                seen.add(sources[rank].chunk_id)
                merged.append(sources[rank])
                if len(merged) >= cap:
                    break
        if len(merged) >= cap:
            break
    for i, s in enumerate(merged, start=1):
        s.n = i
    return merged


def retrieve_for(questions: list[str], k: int = 6) -> list[list[Source]]:
    return [retrieve_sources(q, k) for q in questions]


def _sources_block(sources: list[Source]) -> str:
    return "\n\n".join(format_source(replace(s, text=s.text[:1800])) for s in sources)


def _with_data_rule(system: str) -> str:
    return f"{system}\n{DATA_RULE}" if get_settings().injection_guard else system


FINDING_SYSTEM = """Odpovedáš na jednu podotázku VÝHRADNE zo zdrojov. 1–3 vety, za každé
tvrdenie citácia [n]. Ak zdroje odpoveď neobsahujú, napíš to jednou vetou. Píš v jazyku otázky."""


def answer_subquestion(question: str, sources: list[Source]) -> str:
    try:
        return generate_sync(
            get_settings().answer_model,
            f"Zdroje:\n{_sources_block(sources)}\n\nPodotázka: {question}",
            system=_with_data_rule(FINDING_SYSTEM),
            temperature=0.2,
            num_ctx=8192,
            timeout=180,
        ).strip()
    except OllamaError as exc:
        logger.warning("zistenie k podotázke zlyhalo: %s", exc)
        return ""


SYNTHESIS_SYSTEM = """Si analytik. Z poskytnutých zistení a zdrojov napíš štruktúrovanú správu
v markdowne s nadpismi ## (Zhrnutie, Zistenia, Súvislosti medzi dokumentmi, Otvorené otázky).
Pravidlá:
- Každé tvrdenie musí mať citáciu [n] na číslo zdroja.
- Čísla a dátumy uvádzaj presne ako v zdrojoch.
- V časti Súvislosti pomenuj, ktoré dokumenty sa navzájom dopĺňajú alebo si odporujú.
- V časti Otvorené otázky uveď, čo zdroje nepokrývajú.
- Píš v jazyku pôvodnej otázky. Bez úvodných fráz."""


def synthesis_messages(
    question: str, findings: list[tuple[str, str]], sources: list[Source]
) -> list[dict[str, str]]:
    notes = "\n".join(f"- {q}\n  → {a}" for q, a in findings if a)
    user = (
        f"Pôvodná otázka: {question}\n\nZistenia k podotázkam:\n{notes}\n\n"
        f"Zdroje:\n{_sources_block(sources)}"
    )
    return [
        {"role": "system", "content": _with_data_rule(SYNTHESIS_SYSTEM)},
        {"role": "user", "content": user},
    ]


def sections_from_markdown(markdown: str) -> list[tuple[str, str]]:
    """'## Heading\\ntext' → [(heading, text)] — for exporting the answer to PDF."""
    sections: list[tuple[str, str]] = []
    heading: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        m = re.match(r"^\s*#{1,3}\s+(.+?)\s*#*\s*$", line)
        if m:
            if heading is not None or "".join(body).strip():
                sections.append((heading or "Odpoveď", "\n".join(body).strip()))
            heading, body = m.group(1), []
        else:
            body.append(line)
    if heading is not None or "".join(body).strip():
        sections.append((heading or "Odpoveď", "\n".join(body).strip()))
    return [(h, b) for h, b in sections if b]


def subquestion_event(i: int, total: int, question: str) -> dict[str, Any]:
    return {"type": "step", "tool": "subquestion", "summary": f"{i}/{total} {question}"}

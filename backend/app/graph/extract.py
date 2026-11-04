"""Entity extraction — deterministic first (exact, free), then LLM.

Deterministic: quantities with units, citations (DOI, arXiv, standards).
Numbers NEVER go through the LLM — regex over the parser text.
LLM (qwen3:1.7b, structured output): technical terms/methods/materials.
"""

import logging
import re
import unicodedata

from pydantic import ValidationError

from app.config import get_settings
from app.graph.schema import CONCEPT_JSON_SCHEMA, ConceptExtraction, ExtractedConcept
from app.llm.ollama_client import OllamaError, generate_sync

logger = logging.getLogger(__name__)

UNIT_PATTERN = re.compile(
    r"(?<![\w/])(\d+(?:[.,]\d+)?)\s*"
    r"(l/min|m/s|km/h|kPa|MPa|GPa|hPa|bar|mbar|°C|°F|K\b|mm|cm|km|m\b|µm|nm|"
    r"kg|mg|µg|g\b|t\b|Hz|kHz|MHz|GHz|mV|kV|V\b|mA|kA|A\b|kW|MW|W\b|kWh|J\b|kJ|"
    r"%|rpm|ms|µs|ns|s\b|min\b|h\b|l\b|ml|mol|ppm|dB)",
    re.UNICODE,
)

DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+")
ARXIV_PATTERN = re.compile(r"\barXiv:\s?(\d{4}\.\d{4,5})", re.IGNORECASE)
NORM_PATTERN = re.compile(r"\b(?:STN|ISO|EN|IEC|DIN|ASTM)(?:\s?[A-Z]{1,4})?\s?\d{2,6}(?:[-:]\d+)?")

EXTRACT_SYSTEM = (
    "Extrahuješ odborné pojmy z technického textu. Vraciaš iba JSON podľa schémy. "
    "Pojmy uvádzaj v základnom tvare, v jazyku textu."
)
EXTRACT_PROMPT = """Text:
{chunk}

Extrahuj najviac 8 kľúčových odborných pojmov (metódy, materiály, zariadenia, koncepty).
Ignoruj bežné slová a čísla. Pre každý pojem urči, či ho text priamo definuje/vysvetľuje
(defined_here) alebo len spomína."""

MAX_CHUNK_CHARS = 3000


def normalize_key(name: str) -> str:
    """Normalized key for node sharing: casefold + no diacritics + no punctuation."""
    nfkd = unicodedata.normalize("NFKD", name.casefold().strip())
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", stripped).strip("-")


def extract_quantities(text: str) -> list[tuple[str, str, str]]:
    """→ [(norm_key, label, unit)] — unique unit occurrences with a value."""
    out: dict[str, tuple[str, str, str]] = {}
    for m in UNIT_PATTERN.finditer(text):
        value, unit = m.group(1), m.group(2).strip()
        label = f"{value} {unit}"
        key = normalize_key(label)
        out[key] = (key, label, unit)
    return list(out.values())


def extract_references(text: str) -> list[tuple[str, str, str]]:
    """→ [(norm_key, label, ref_type)]"""
    refs: dict[str, tuple[str, str, str]] = {}
    for m in DOI_PATTERN.finditer(text):
        label = m.group(0).rstrip(".,;)")
        refs[normalize_key(label)] = (normalize_key(label), label, "doi")
    for m in ARXIV_PATTERN.finditer(text):
        label = f"arXiv:{m.group(1)}"
        refs[normalize_key(label)] = (normalize_key(label), label, "arxiv")
    for m in NORM_PATTERN.finditer(text):
        label = re.sub(r"\s+", " ", m.group(0)).strip()
        refs[normalize_key(label)] = (normalize_key(label), label, "norm")
    return list(refs.values())


def extract_concepts_llm(text: str) -> list[ExtractedConcept]:
    settings = get_settings()
    prompt = EXTRACT_PROMPT.format(chunk=text[:MAX_CHUNK_CHARS])
    for attempt in (1, 2):
        try:
            raw = generate_sync(
                settings.extract_model,
                prompt,
                system=EXTRACT_SYSTEM,
                json_schema=CONCEPT_JSON_SCHEMA,
                num_ctx=4096,
            )
            parsed = ConceptExtraction.model_validate_json(raw)
            return [c for c in parsed.concepts if len(c.name.strip()) >= 2]
        except (OllamaError, ValidationError) as exc:
            logger.warning("LLM extrakcia pokus %s zlyhal: %s", attempt, exc)
    return []

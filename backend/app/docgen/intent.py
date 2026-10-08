"""Rozpoznanie požiadavky „vytvor dokument…“ — deterministické heuristiky.

Generovanie je drahé (niekoľko LLM volaní + render), preto sa spúšťa len pri
jasnom príkaze: sloveso tvorby + typ dokumentu, alebo výslovne „PDF“.
"""

import re
import unicodedata
from dataclasses import dataclass

from app.docgen.schema import DocType, Lang

SK_VERBS = r"vytvor|vygeneruj|sprav|spravte|priprav|napis|zostav|vypracuj|vyhotov|exportuj"
EN_VERBS = r"create|generate|draft|write|prepare|make|produce|export"

# poradie = priorita pri viacerých zhodách
DOC_NOUNS: list[tuple[DocType, str]] = [
    ("offer", r"ponuk\w*|cenov\w*|offer|proposal|quote|quotation"),
    ("memo", r"memo\w*|obeznik\w*"),
    ("summary", r"suhrn\w*|zhrn\w*|summary|overview"),
    ("report", r"report\w*|spravu|sprava|spravy|analyz\w*|analysis"),
    ("free", r"dokument\w*|document|pdf|list|letter|clan\w*|article"),
]


@dataclass
class GenerateIntent:
    doc_type: DocType
    lang: Lang


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def detect_generate_intent(question: str) -> GenerateIntent | None:
    qn = _norm(question)
    sk_verb = re.search(rf"\b(?:{SK_VERBS})\b", qn)
    en_verb = re.search(rf"\b(?:{EN_VERBS})\b", qn)
    explicit_pdf = re.search(r"\b(?:do|ako|v|vo|as|to|in)\s+(?:a\s+)?pdf\b", qn)

    doc_type: DocType | None = None
    for dtype, pattern in DOC_NOUNS:
        if re.search(rf"\b(?:{pattern})\b", qn):
            doc_type = dtype
            break

    if doc_type is None or not (sk_verb or en_verb or explicit_pdf):
        return None
    lang: Lang = "en" if en_verb and not sk_verb else "sk"
    return GenerateIntent(doc_type=doc_type, lang=lang)

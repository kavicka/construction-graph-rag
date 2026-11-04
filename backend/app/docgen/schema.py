"""Generated document types and their outlines."""

from dataclasses import dataclass, field
from typing import Literal

DocType = Literal["free", "report", "offer", "memo", "summary"]
Lang = Literal["sk", "en"]

DOC_TYPE_LABELS: dict[Lang, dict[DocType, str]] = {
    "sk": {
        "free": "Dokument",
        "report": "Report",
        "offer": "Cenová ponuka",
        "memo": "Interné memo",
        "summary": "Súhrn dokumentov",
    },
    "en": {
        "free": "Document",
        "report": "Report",
        "offer": "Proposal",
        "memo": "Internal memo",
        "summary": "Document summary",
    },
}

# fixed template outlines; for "free" the LLM proposes the outline
OUTLINES: dict[Lang, dict[DocType, list[str]]] = {
    "sk": {
        "free": [],
        "report": ["Zhrnutie", "Zistenia", "Súvislosti medzi dokumentmi", "Odporúčania"],
        "offer": ["Úvod", "Predmet ponuky", "Cena a podmienky", "Ďalší postup"],
        "memo": ["Kontext", "Hlavné body", "Ďalšie kroky"],
        "summary": ["Prehľad", "Kľúčové informácie", "Súvislosti medzi dokumentmi", "Záver"],
    },
    "en": {
        "free": [],
        "report": ["Executive summary", "Findings", "Cross-document links", "Recommendations"],
        "offer": ["Introduction", "Scope", "Pricing and terms", "Next steps"],
        "memo": ["Context", "Key points", "Next steps"],
        "summary": ["Overview", "Key information", "Cross-document links", "Conclusion"],
    },
}

UI_TEXT: dict[Lang, dict[str, str]] = {
    "sk": {
        "sources": "Zdroje",
        "page": "str.",
        "prepared_by": "Pripravil",
        "date": "Dátum",
        "page_of": "Strana {page} z {pages}",
        "contact": "Kontakt",
        "bank": "Bankové spojenie",
    },
    "en": {
        "sources": "Sources",
        "page": "p.",
        "prepared_by": "Prepared by",
        "date": "Date",
        "page_of": "Page {page} of {pages}",
        "contact": "Contact",
        "bank": "Bank account",
    },
}


@dataclass
class DocSection:
    heading: str
    body_md: str


@dataclass
class DocumentContent:
    doc_type: DocType
    lang: Lang
    title: str
    subtitle: str = ""
    sections: list[DocSection] = field(default_factory=list)

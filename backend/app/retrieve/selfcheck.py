"""Self-check odpovede — každé podstatné číslo v odpovedi musí existovať
v podkladoch (zdroje / výsledky nástrojov). Deterministická posledná obrana
proti halucinovaným číslam malého modelu.
"""

import re

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
CITATION_RE = re.compile(r"\[\d+\]")
MIN_SIGNIFICANT = 13  # malé čísla (počty, poradia, citácie) nekontrolujeme


def _canon(token: str) -> float:
    return float(token.replace(",", "."))


def check_numbers(answer: str, evidence_texts: list[str]) -> list[str]:
    """→ zoznam čísel z odpovede, ktoré sa nenachádzajú v podkladoch."""
    allowed = {_canon(n) for text in evidence_texts for n in NUM_RE.findall(text)}
    # súčty legitímne vznikajú v pick_items — tie sú v evidence; iné súčty povolíme,
    # len ak sú súčtom dvojice/trojice povolených čísel
    pair_sums = {a + b for a in allowed for b in allowed}

    stripped = CITATION_RE.sub(" ", answer)
    issues = []
    for token in NUM_RE.findall(stripped):
        value = _canon(token)
        if value < MIN_SIGNIFICANT:
            continue
        if value in allowed or value in pair_sums:
            continue
        issues.append(token)
    return sorted(set(issues))

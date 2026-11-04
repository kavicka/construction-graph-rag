"""Answer self-check — every significant number in the answer must exist
in the evidence (sources / tool results). A deterministic last line of defence
against numbers hallucinated by the small model.
"""

import re

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
CITATION_RE = re.compile(r"\[\d+\]")
MIN_SIGNIFICANT = 13  # small numbers (counts, ranks, citations) are not checked


def _canon(token: str) -> float:
    return float(token.replace(",", "."))


def check_numbers(answer: str, evidence_texts: list[str]) -> list[str]:
    """→ list of numbers from the answer that are not found in the evidence."""
    allowed = {_canon(n) for text in evidence_texts for n in NUM_RE.findall(text)}
    # sums legitimately arise in pick_items — those are in the evidence; other sums are allowed
    # only if they are the sum of a pair/triple of allowed numbers
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

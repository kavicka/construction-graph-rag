"""Pure functions for metrics — tested in tests/test_eval_metrics.py.

Retrieval relevance is at the (document, page) level: a chunk is relevant if it is
from the gold document and its page range covers the gold page.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

REFUSAL_HINTS = (
    "found nothing",  # the app's fixed reply (scope_guard.NOT_FOUND_REPLY_EN)
    "ziadne informacie",
    "nenachadza",
    "nenašiel",
    "nenasiel",
    "neuvadza",
    "neobsahuj",
    "nie je uveden",
    "nie su uveden",
    "nemam informac",
    "nie je k dispozic",
    "chyba informac",
    "nie je informac",
    "nie su informac",
    "neposkytuj",
    "neuvedene",
    "nemozno urcit",
    "nemozem urcit",
    "nie je mozne urcit",
    "not mention",
    "not contain",
    "no information",
    "not provided",
    "not specified",
    "does not say",
    "not available",
    "cannot find",
    "isn't mentioned",
    "is not mentioned",
)
CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass
class RetrievedChunk:
    filename: str
    page_from: int
    page_to: int


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_answer(text: str) -> str:
    """Normalises the notation of numbers and dates: '4 860 000' → '4860000', '0,05' → '0.05',
    '30. 11. 2027' → '30.11.2027'; without diacritics, lowercase."""
    t = _strip_accents(text.replace(" ", " ").replace(" ", " ")).casefold()
    t = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", t)  # thousands separator
    t = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", t)  # 2. pass: 1 742 300
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)  # English thousands separator 386,500
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)
    t = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", t)
    return re.sub(r"\s+", " ", t)


def contains_expected(answer: str, expected: str) -> bool:
    """`expected` may have alternatives separated by `|` („4 August 2026|4. augusta 2026“)."""
    if "|" in expected:
        return any(contains_expected(answer, alt) for alt in expected.split("|"))
    a, e = normalize_answer(answer), normalize_answer(expected)
    if len(e) <= 4 and re.fullmatch(r"[\w.]+", e):
        # short tokens ('5', 'E', 'A0') only as a standalone word — otherwise '5' matches everywhere
        return re.search(rf"(?<![\w.]){re.escape(e)}(?![\w]|\.\d)", a) is not None
    return e in a


REFUSAL_MAX_CHARS = 300


def is_refusal(answer: str) -> bool:
    """A short answer saying the sources do not have the information. A longer answer with such
    a sentence is a partial answer (one part is missing), not a refusal."""
    a = _strip_accents(answer).casefold()
    return any(h in a for h in REFUSAL_HINTS) and len(answer.strip()) <= REFUSAL_MAX_CHARS


def _relevant(chunk: RetrievedChunk, gold: Sequence[tuple[str, int]]) -> set[tuple[str, int]]:
    return {
        (doc, page)
        for doc, page in gold
        if chunk.filename == doc and chunk.page_from <= page <= chunk.page_to
    }


def recall_at_k(ranked: Sequence[RetrievedChunk], gold: Sequence[tuple[str, int]], k: int) -> float:
    """Share of gold (document, page) pairs covered in the top-k."""
    if not gold:
        return math.nan
    found: set[tuple[str, int]] = set()
    for chunk in ranked[:k]:
        found |= _relevant(chunk, gold)
    return len(found) / len(set(gold))


def reciprocal_rank(ranked: Sequence[RetrievedChunk], gold: Sequence[tuple[str, int]]) -> float:
    if not gold:
        return math.nan
    for i, chunk in enumerate(ranked, start=1):
        if _relevant(chunk, gold):
            return 1.0 / i
    return 0.0


def ndcg_at_k(ranked: Sequence[RetrievedChunk], gold: Sequence[tuple[str, int]], k: int) -> float:
    """Binary relevance; a chunk covering an already counted gold page adds no gain."""
    if not gold:
        return math.nan
    seen: set[tuple[str, int]] = set()
    dcg = 0.0
    for i, chunk in enumerate(ranked[:k], start=1):
        new = _relevant(chunk, gold) - seen
        if new:
            seen |= new
            dcg += 1.0 / math.log2(i + 1)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(set(gold)), k) + 1))
    return dcg / ideal


def mean(values: Sequence[float]) -> float:
    vals = [v for v in values if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else math.nan


def citation_validity(answer: str, n_sources: int) -> bool | None:
    """True if all citations point to an existing source; None if the answer has no citations."""
    nums = [int(x) for m in CITATION.finditer(answer) for x in m.group(1).split(",")]
    if not nums:
        return None
    return all(1 <= n <= n_sources for n in nums)

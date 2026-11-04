"""Čisté funkcie na metriky — testované v tests/test_eval_metrics.py.

Retrieval relevancia je na úrovni (dokument, strana): chunk je relevantný, ak je
z gold dokumentu a jeho rozsah strán pokrýva gold stranu.
"""

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

REFUSAL_HINTS = (
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
    """Zjednotí zápis čísel a dátumov: '4 860 000' → '4860000', '0,05' → '0.05',
    '30. 11. 2027' → '30.11.2027'; bez diakritiky, malé písmená."""
    t = _strip_accents(text.replace(" ", " ").replace(" ", " ")).casefold()
    t = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", t)  # oddeľovač tisícov
    t = re.sub(r"(?<=\d)[ ](?=\d{3}\b)", "", t)  # 2. prechod: 1 742 300
    t = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)  # anglický oddeľovač tisícov 386,500
    t = re.sub(r"(?<=\d),(?=\d)", ".", t)
    t = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", t)
    return re.sub(r"\s+", " ", t)


def contains_expected(answer: str, expected: str) -> bool:
    """`expected` môže mať alternatívy oddelené `|` („4 August 2026|4. augusta 2026“)."""
    if "|" in expected:
        return any(contains_expected(answer, alt) for alt in expected.split("|"))
    a, e = normalize_answer(answer), normalize_answer(expected)
    if len(e) <= 4 and re.fullmatch(r"[\w.]+", e):
        # krátke tokeny ('5', 'E', 'A0') len ako samostatné slovo — inak '5' sedí všade
        return re.search(rf"(?<![\w.]){re.escape(e)}(?![\w]|\.\d)", a) is not None
    return e in a


REFUSAL_MAX_CHARS = 300


def is_refusal(answer: str) -> bool:
    """Krátka odpoveď, ktorá hovorí, že podklady informáciu nemajú. Dlhšia odpoveď s takou
    vetou je čiastočná odpoveď (jedna časť chýba), nie odmietnutie."""
    a = _strip_accents(answer).casefold()
    return any(h in a for h in REFUSAL_HINTS) and len(answer.strip()) <= REFUSAL_MAX_CHARS


def _relevant(chunk: RetrievedChunk, gold: Sequence[tuple[str, int]]) -> set[tuple[str, int]]:
    return {
        (doc, page)
        for doc, page in gold
        if chunk.filename == doc and chunk.page_from <= page <= chunk.page_to
    }


def recall_at_k(ranked: Sequence[RetrievedChunk], gold: Sequence[tuple[str, int]], k: int) -> float:
    """Podiel gold (dokument, strana) pokrytých v top-k."""
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
    """Binárna relevancia; chunk, ktorý pokryje už započítanú gold stranu, nezvyšuje zisk."""
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
    """True ak všetky citácie odkazujú na existujúci zdroj; None ak odpoveď necituje."""
    nums = [int(x) for m in CITATION.finditer(answer) for x in m.group(1).split(",")]
    if not nums:
        return None
    return all(1 <= n <= n_sources for n in nums)

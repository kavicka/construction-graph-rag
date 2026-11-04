"""Sentence-by-sentence answer verification — does each sentence have support in its cited source?

For a sentence with citations [n], the most similar sentence in the cited sources is found
(embedding, cosine) and its numbers are checked. Per-sentence result:
  supported   — a similar sentence in the cited source and the numbers match
  weak        — a citation exists, but the source clearly does not match the sentence
                (low similarity / different number)
  uncited     — a factual sentence without a citation
Refusals („podklady to neuvádzajú“) and short connecting sentences are not evaluated.

It is a cheap heuristic (no LLM call), not proof — the UI only uses it to flag
which sentences should be checked in the PDF.
"""

import math
import re
from dataclasses import asdict, dataclass

from app.index.embedder import get_embedder
from app.retrieve.pipeline import Source
from app.retrieve.selfcheck import NUM_RE

CITE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
BOUNDARY = re.compile(r"[.!?…]\s+(?=[A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ0-9\-•*\[])")
# a dot after these words does not end a sentence („Ing. Peter Hronec“, „č. 3“, „s.r.o. Žilina“)
ABBREVIATIONS = {
    "ing",
    "mgr",
    "bc",
    "dr",
    "phdr",
    "judr",
    "mudr",
    "doc",
    "prof",
    "napr",
    "tzn",
    "resp",
    "cca",
    "č",
    "str",
    "ods",
    "písm",
    "kap",
    "obr",
    "tab",
    "max",
    "min",
    "s.r.o",
    "a.s",
    "spol",
    "t",
    "j",
    "vs",
    "e.g",
    "i.e",
    "mr",
    "mrs",
    "ms",
    "no",
    "p",
    "pp",
    "approx",
    "st",
}
SUPPORT_THRESHOLD = 0.65  # bge-m3: supported sentences ≥ 0.78, unsupported ≤ 0.56 (eval corpus)
MIN_FACT_CHARS = 25
MAX_SENTENCES = 30
REFUSAL = re.compile(
    r"neuvádza|nenachádza|nie je uveden|nie je informáci|nemám informác|neobsahuj|"
    r"neposkytuj|not mention|not contain|no information|not provided|not specified",
    re.IGNORECASE,
)


@dataclass
class SentenceCheck:
    text: str
    status: str  # supported | weak | uncited
    score: float | None
    citations: list[int]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _ends_sentence(text: str, dot: int) -> bool:
    """Is the dot at position `dot` a sentence end, or an abbreviation / ordinal number?"""
    if text[dot] != ".":
        return True
    word = re.search(r"(\S+)$", text[:dot])
    token = word.group(1).casefold() if word else ""
    if token.strip("(") in ABBREVIATIONS:
        return False
    # ordinal number / date: „2. NP“, „30. 11. 2027“ — the number is followed by a number or
    # an abbreviation in capitals
    nxt = text[dot + 1 :].lstrip()
    return not (token.isdigit() and re.match(r"(\d|[A-Z]{1,3}\b)", nxt))


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for block in text.splitlines():
        start = 0
        for m in BOUNDARY.finditer(block):
            if _ends_sentence(block, m.start()):
                sentences.append(block[start : m.start() + 1])
                start = m.end()
        sentences.append(block[start:])
    parts = [p.strip(" -•*\t") for p in sentences]
    return [p for p in parts if p]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in NUM_RE.findall(CITE.sub(" ", text))}


def verify_sentences(answer: str, sources: list[Source]) -> list[SentenceCheck]:
    by_n = {s.n: s for s in sources}
    sentences = split_sentences(answer)[:MAX_SENTENCES]
    checks: list[SentenceCheck] = []
    to_score: list[tuple[int, str, list[str]]] = []  # (index in checks, sentence, source sentences)

    for sentence in sentences:
        cites = [int(n) for m in CITE.finditer(sentence) for n in m.group(1).split(",")]
        bare = CITE.sub("", sentence).strip()
        if len(bare) < MIN_FACT_CHARS or REFUSAL.search(bare):
            continue
        cited = [by_n[n] for n in cites if n in by_n]
        if not cited:
            checks.append(SentenceCheck(bare, "uncited", None, cites))
            continue
        source_sentences = [s for src in cited for s in split_sentences(src.text) if len(s) >= 10][
            :200
        ]
        if not source_sentences:
            checks.append(SentenceCheck(bare, "weak", None, cites))
            continue
        checks.append(SentenceCheck(bare, "supported", None, cites))
        to_score.append((len(checks) - 1, bare, source_sentences))

    if not to_score:
        return checks

    embedder = get_embedder()
    flat = [s for _, _, srcs in to_score for s in srcs]
    src_vecs = embedder.embed_passages(flat)
    offset = 0
    for idx, sentence, srcs in to_score:
        vec = embedder.embed_query(sentence)
        window = src_vecs[offset : offset + len(srcs)]
        offset += len(srcs)
        best = max(_cosine(vec, v) for v in window)
        cited_text = " ".join(srcs)
        numbers_ok = _numbers(sentence) <= _numbers(cited_text)
        check = checks[idx]
        check.score = round(best, 3)
        if best < SUPPORT_THRESHOLD or not numbers_ok:
            check.status = "weak"
    return checks


def is_refusal_answer(answer: str) -> bool:
    """A short answer such as „podklady to neuvádzajú“."""
    return len(answer.strip()) <= 300 and bool(REFUSAL.search(answer))

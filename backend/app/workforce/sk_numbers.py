"""Slovak numbers and times in spoken language — deterministic check of LLM output.

From the transcript the LLM extracts „príchod 07:30, murovanie 5 h“ (arrival 07:30, bricklaying 5
h). Here we verify that the person really said it: the time 07:30 must correspond in the text to
e.g. „o pol ôsmej“, „7:30“ or „o pol 8“, and 5 hours to „päť hodín“ or „od 7 do 12“. An unsupported
number is dropped and the system prefers to ask again. Speech transcripts contain errors, so ordinal
numerals are compared fuzzily."""

import re
import unicodedata
from functools import lru_cache

from rapidfuzz import fuzz, process

CARDINALS: dict[str, int] = {
    "nula": 0,
    "jeden": 1,
    "jedna": 1,
    "jedno": 1,
    "jednu": 1,
    "dva": 2,
    "dve": 2,
    "dvoch": 2,
    "tri": 3,
    "troch": 3,
    "styri": 4,
    "styroch": 4,
    "pat": 5,
    "piatich": 5,
    "sest": 6,
    "siestich": 6,
    "sedem": 7,
    "osem": 8,
    "devat": 9,
    "desat": 10,
    "jedenast": 11,
    "dvanast": 12,
    "trinast": 13,
    "strnast": 14,
    "patnast": 15,
    "sestnast": 16,
    "sedemnast": 17,
    "osemnast": 18,
    "devatnast": 19,
    "dvadsat": 20,
    "tridsat": 30,
    "styridsat": 40,
    "patdesiat": 50,
    "sestdesiat": 60,
    "sedemdesiat": 70,
    "osemdesiat": 80,
    "devatdesiat": 90,
    "sto": 100,
}
TENS = {
    "dvadsat": 20,
    "tridsat": 30,
    "styridsat": 40,
    "patdesiat": 50,
    "sestdesiat": 60,
    "sedemdesiat": 70,
    "osemdesiat": 80,
    "devatdesiat": 90,
}
UNITS = {k: v for k, v in CARDINALS.items() if 1 <= v <= 9}

# „o siedmej“, „pol ôsmej“ — genitive of an ordinal numeral = hour
ORDINALS: dict[str, int] = {
    "jednej": 1,
    "prvej": 1,
    "druhej": 2,
    "tretej": 3,
    "stvrtej": 4,
    "piatej": 5,
    "siestej": 6,
    "siedmej": 7,
    "osmej": 8,
    "deviatej": 9,
    "desiatej": 10,
    "jedenastej": 11,
    "dvanastej": 12,
}
ORDINAL_FUZZY_MIN = 85

# fractions and word forms of quantity
FRACTION_WORDS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"\bpoldruha\b"), 1.5),
    (re.compile(r"\b(polhodin\w*|polhodk\w*)\b"), 0.5),
    (re.compile(r"\btri ?stvrte\b"), 0.75),
    (re.compile(r"\bstvrt (hodin\w*|hodk\w*)\b"), 0.25),
    (re.compile(r"\b(hodinu|hodinku|hodina)\b"), 1.0),
    (re.compile(r"\bpolovic\w*\b"), 50.0),
    (re.compile(r"\btri ?stvrtin\w*\b"), 75.0),
    (re.compile(r"\bstvrtin\w*\b"), 25.0),
    (re.compile(r"\bdve ?tretin\w*\b"), 66.0),
    (re.compile(r"\btretin\w*\b"), 33.0),
]

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
# Whisper glues the preposition to the numeral: „Opol 7“ = „o pol 7“
STT_FIXES = [
    (re.compile(r"\bopol\b"), "o pol"),
    (re.compile(r"\bodpol\b"), "od pol"),
    (re.compile(r"\bpolu\b"), "pol"),  # „od polu ôsmej“
    # „o štvrti na sedem“ (quarter to seven), Czech
    (re.compile(r"\b(stvrti|ctvrt|ctvrti)\b"), "stvrt"),
    (re.compile(r"\bctvrte\b"), "stvrte"),
]
HHMM_RE = re.compile(r"\b(\d{1,2})[:.](\d{2})\b")


def normalize(text: str) -> str:
    """Lowercase, no diacritics, punctuation → spaces (except : and . between digits)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    plain = re.sub(r"(?<=\d)[:.](?=\d)", ":", plain)
    plain = re.sub(r"[^\w:%]+", " ", plain)
    for wrong, right in STT_FIXES:
        plain = wrong.sub(right, plain)
    return re.sub(r"\s+", " ", plain).strip()


@lru_cache(maxsize=4096)
def _ordinal(token: str) -> int | None:
    if token in ORDINALS:
        return ORDINALS[token]
    if len(token) < 4:
        return None
    match = process.extractOne(
        token, ORDINALS.keys(), scorer=fuzz.ratio, score_cutoff=ORDINAL_FUZZY_MIN
    )
    return ORDINALS[match[0]] if match else None


def _word_number(tokens: list[str], i: int) -> tuple[float, int] | None:
    """Number from words at position i → (value, number of tokens consumed)."""
    tok = tokens[i]
    digit = NUM_RE.fullmatch(tok)
    if digit:
        value = float(tok.replace(",", "."))
        used = 1
    elif tok in TENS and i + 1 < len(tokens) and tokens[i + 1] in UNITS:
        value, used = TENS[tok] + UNITS[tokens[i + 1]], 2
    elif tok in CARDINALS:
        value, used = CARDINALS[tok], 1
    else:
        # compound in one word: „dvadsatpat“
        for ten, tv in TENS.items():
            if tok.startswith(ten) and tok[len(ten) :] in UNITS:
                return float(tv + UNITS[tok[len(ten) :]]), 1
        return None
    # „dve a pol“ (two and a half) / „5 a pol“
    if i + used + 1 < len(tokens) and tokens[i + used] == "a" and tokens[i + used + 1] == "pol":
        return float(value) + 0.5, used + 2
    return float(value), used


def number_candidates(text: str) -> set[float]:
    """All quantities mentioned in the text (digits, words, fractions)."""
    norm = normalize(text)
    tokens = norm.replace(":", " : ").split()
    found: set[float] = set()
    i = 0
    while i < len(tokens):
        parsed = _word_number(tokens, i)
        if parsed:
            found.add(parsed[0])
            i += parsed[1]
        else:
            i += 1
    for pattern, value in FRACTION_WORDS:
        if pattern.search(norm):
            found.add(value)
    # a standalone „pol“ (pol hodiny — half an hour) = 0.5 — not „dve a pol“ (that is 2.5 above);
    # "a" as a conjunction („piesok a pol hodiny“ — sand and half an hour) does not cancel the half
    for j, tok in enumerate(tokens):
        if tok == "pol":
            part_of_number = j >= 2 and tokens[j - 1] == "a" and _word_number(tokens, j - 2)
            if not part_of_number:
                found.add(0.5)
    for m in NUM_RE.finditer(norm):
        found.add(float(m.group().replace(",", ".")))
    return found


def _clock(hour: int, minute: int) -> tuple[int, int] | None:
    if 0 <= hour <= 24 and 0 <= minute < 60:
        return hour % 12, minute
    return None


def time_candidates(text: str) -> set[tuple[int, int]]:
    """Times (hour mod 12, minute) mentioned in the text."""
    norm = normalize(text)
    tokens = norm.replace(":", " : ").split()
    found: set[tuple[int, int]] = set()

    for m in HHMM_RE.finditer(norm):
        if c := _clock(int(m.group(1)), int(m.group(2))):
            found.add(c)

    def hour_at(j: int) -> int | None:
        if j >= len(tokens):
            return None
        tok = tokens[j]
        if tok.isdigit() and int(tok) <= 24 and not (j + 1 < len(tokens) and tokens[j + 1] == ":"):
            return int(tok)
        if tok in CARDINALS and 1 <= CARDINALS[tok] <= 12:
            return CARDINALS[tok]
        return _ordinal(tok)

    for j, tok in enumerate(tokens):
        nxt = tokens[j + 1] if j + 1 < len(tokens) else ""
        if tok == "pol" and (h := hour_at(j + 1)) is not None:
            found.add(((h - 1) % 12, 30))
        elif tok == "stvrt" and nxt == "na" and (h := hour_at(j + 2)) is not None:
            prev = tokens[j - 1] if j else ""
            found.add(((h - 1) % 12, 45 if prev == "tri" else 15))
        elif tok == "stvrte" and nxt == "na" and (h := hour_at(j + 2)) is not None:
            found.add(((h - 1) % 12, 45))
        elif tok in ("o", "od", "do", "po", "okolo", "cca") and (h := hour_at(j + 1)) is not None:
            follow = tokens[j + 2] if j + 2 < len(tokens) else ""
            if follow not in (":", "stvrte", "stvrt"):  # „o tri štvrte na štyri“ (3:45) is not 3:00
                found.add((h % 12, 0))
        elif _ordinal(tok) is not None and nxt.startswith("hodin"):
            found.add(((_ordinal(tok) or 0) % 12, 0))
    return found


def parse_hhmm(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return (h, mi) if 0 <= h <= 23 and 0 <= mi < 60 else None


def time_supported(value: str | None, text: str) -> bool:
    hm = parse_hhmm(value)
    return hm is not None and (hm[0] % 12, hm[1]) in time_candidates(text)


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def hours_supported(value: float, text: str) -> bool:
    """The number of hours was said directly, or it is the difference of two spoken times (od 7 do
    12)."""
    if any(_close(value, c, 0.01) for c in number_candidates(text)):
        return True
    times = time_candidates(text)
    for h1, m1 in times:
        for h2, m2 in times:
            diff = ((h2 * 60 + m2) - (h1 * 60 + m1)) % (12 * 60)
            if diff and _close(value, diff / 60, 0.01):
                return True
    return False


def minutes_supported(value: int, text: str) -> bool:
    cands = number_candidates(text)
    return any(_close(value, c, 0.5) or _close(value / 60, c, 0.01) for c in cands)


def percent_supported(value: int, text: str) -> bool:
    return any(_close(value, c, 1.0) for c in number_candidates(text))


def to_24h(hm: tuple[int, int], *, afternoon_hint: bool) -> tuple[int, int]:
    """Hour without am/pm: working day 5:00–21:00.

    afternoon_hint = it is a departure (almost always in the afternoon)."""
    h, m = hm
    if h >= 13:
        return h, m
    if afternoon_hint and h <= 9:
        return h + 12, m
    if not afternoon_hint and h <= 4:
        return h + 12, m
    return h, m


NEGATIVE_RE = re.compile(
    r"\b(nie|nebol\w*|ziadn\w*|nic|bez (problem\w*|prestavk\w*)|nemal\w*|netreba"
    r"|vsetko (ok|v poriadku|fajn)|v poriadku|ok)\b"
)
# „nebolo lepidlo“ (there was no glue) is a problem, not a negation — content words rule out a short
# answer
PROBLEM_HINT_RE = re.compile(
    r"\b(chyb\w*|pokazil\w*|porucha|poruch\w*|uraz\w*|zranil\w*|meska\w*|neprisl\w*"
    r"|nedoviez\w*|nedodal\w*|prsal\w*|prsi|dazd\w*|mraz\w*|vietor|lepidl\w*|material\w*"
    r"|beton\w*|cement\w*|stroj\w*|bager\w*|zerav\w*)\b"
)
SHORT_ANSWER_TOKENS = 6


def is_short_negative(text: str) -> bool:
    """Short „nie“ / „žiadne“ / „bez problémov“ (no / none / no problems) with no content words →
    the answer is nothing."""
    norm = normalize(text)
    return (
        bool(NEGATIVE_RE.search(norm))
        and len(norm.split()) <= SHORT_ANSWER_TOKENS
        and not PROBLEM_HINT_RE.search(norm)
    )

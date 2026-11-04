"""Who and what is in the question: worker (in any grammatical case), construction site, task.

„Mikuša“, „Mikušovi“, „Petra“ (Peter), „Marka“ (Marek), „Kráľovej“ (Kráľová) — declension
is handled by comparing word stems, not by a list of forms. A surname that is also an ordinary
word or profession („Malý“, „Tesár“) counts only when it appears capitalised in the question
or together with the first name.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.workforce.qa.data import SiteRef, TaskFact, WorkerRef
from app.workforce.sk_dates import MONTHS, WEEKDAYS
from app.workforce.store import DEFAULT_PROFESSIONS


def norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", norm(text))


@dataclass(frozen=True)
class QToken:
    norm: str
    capitalized: bool
    first: bool  # first word of the sentence — the capital letter means nothing


def question_tokens(question: str) -> list[QToken]:
    out: list[QToken] = []
    sentence_start = True
    for m in re.finditer(r"\w+|[.!?]", question):
        raw = m.group(0)
        if raw in ".!?":
            sentence_start = True
            continue
        out.append(QToken(norm(raw), raw[:1].isupper(), sentence_start))
        sentence_start = False
    return out


def inflected(q: str, name: str) -> int:
    """Match score of a question form vs. a name form: 3 exact, 2 case ending, 1 typo, 0 none."""
    if q == name:
        return 3
    if len(name) < 3 or len(q) < 3:
        return 0
    if q.startswith(name) and len(q) - len(name) <= 4:  # Mikuš → Mikušovi, Ján → Jána
        return 2
    # dropped e/o: Peter → Petra, Marek → Marka, Hronec → Hronca, Pavol → Pavla
    if len(name) >= 4 and name[-2] in "eo":
        stem = name[:-2] + name[-1]
        if q.startswith(stem) and len(q) - len(stem) <= 4:
            return 2
    # final vowel: Jana → Jany, Kráľová → Kráľovej, Novotný → Novotného
    if len(name) >= 4 and name[-1] in "aoyie":
        stem = name[:-1]
        if q.startswith(stem) and len(q) - len(stem) <= 4:
            return 2
    if len(name) >= 5 and fuzz.ratio(q, name) >= 90:
        return 1
    return 0


COMMON_WORDS = {norm(p) for p in DEFAULT_PROFESSIONS} | {
    "maly",
    "velky",
    "novy",
    "stary",
    "biely",
    "cierny",
    "dobry",
    "zly",
    "hora",
    "les",
    "kovac",
    "kral",
    "pan",
    "pani",
    "sused",
    "mlynar",
    "rybar",
    "krajci",
    "sedlak",
    "dnes",
    "zajtra",
    "stavba",
    "uloha",
    "robota",
    "prace",
}


@dataclass
class WorkerMatch:
    worker: WorkerRef
    token: str  # the word from the question that carried the name (for the follow-up question)


@dataclass
class Resolved:
    workers: list[WorkerRef] = field(default_factory=list)
    ambiguous: list[tuple[str, list[WorkerRef]]] = field(default_factory=list)
    name_tokens: set[str] = field(default_factory=set)  # question words used for names


# „január“ must not be Ján, „piatok“ must not be Pia
DATE_WORDS = set(MONTHS) | set(WEEKDAYS) | {"januari", "januar", "dnes", "zajtra", "vcera"}


def _best(q_tokens: list[QToken], name_tok: str) -> tuple[int, QToken | None]:
    best = _best_at(q_tokens, name_tok)
    return best[0], best[1]


def _best_at(q_tokens: list[QToken], name_tok: str) -> tuple[int, QToken | None, int]:
    """(score, word, index of the word in the question)."""
    best: tuple[int, QToken | None, int] = (0, None, -1)
    for i, qt in enumerate(q_tokens):
        if qt.norm in DATE_WORDS and qt.norm != name_tok:
            continue
        score = inflected(qt.norm, name_tok)
        if score > best[0]:
            best = (score, qt, i)
    return best


def _other_surname_follows(q_tokens: list[QToken], idx: int, surname: str) -> bool:
    """„Ján Novák“ for the worker Ján Novotný — a different surname follows the first name,
    so it is a different person (who is then reported as unknown)."""
    if idx < 0 or idx + 1 >= len(q_tokens):
        return False
    nxt = q_tokens[idx + 1]
    return nxt.capitalized and not inflected(nxt.norm, surname)


def match_workers(question: str, workers: list[WorkerRef]) -> Resolved:
    q_tokens = question_tokens(question)
    full: list[WorkerMatch] = []
    surname: list[WorkerMatch] = []
    first: list[WorkerMatch] = []
    for w in workers:
        parts = tokens(w.name)
        if not parts:
            continue
        hits = [_best(q_tokens, p) for p in parts]
        if all(score for score, _ in hits):
            full.append(WorkerMatch(w, " ".join(qt.norm for _, qt in hits if qt)))
            continue
        if len(parts) < 2:
            continue
        s_score, s_tok = hits[-1]
        if (
            s_score
            and s_tok is not None
            and (
                (s_tok.capitalized and not s_tok.first)
                or (s_score >= 2 and parts[-1] not in COMMON_WORDS)
            )
        ):
            surname.append(WorkerMatch(w, s_tok.norm))
            continue
        f_score, f_tok, f_idx = _best_at(q_tokens, parts[0])
        # first name alone: exact form, or declined with a capital letter
        if (
            f_tok is not None
            and parts[0] not in COMMON_WORDS
            and (f_score == 3 or (f_score == 2 and f_tok.capitalized and not f_tok.first))
            and not _other_surname_follows(q_tokens, f_idx, parts[-1])
        ):
            first.append(WorkerMatch(w, f_tok.norm))

    res = Resolved()
    chosen = full or surname
    if chosen:
        res.workers = _dedup([m.worker for m in chosen])
        res.name_tokens = {t for m in chosen for t in m.token.split()}
        return res
    if first:
        by_token: dict[str, list[WorkerRef]] = {}
        for m in first:
            by_token.setdefault(m.token, []).append(m.worker)
        for tok, ws in by_token.items():
            res.name_tokens.add(tok)
            if len(ws) == 1:
                res.workers.append(ws[0])
            else:
                res.ambiguous.append((tok, ws))
    return res


def _dedup(ws: list[WorkerRef]) -> list[WorkerRef]:
    seen: dict[str, WorkerRef] = {}
    for w in ws:
        seen.setdefault(w.id, w)
    return list(seen.values())


SITE_GENERIC = {
    "bytovy",
    "dom",
    "domy",
    "rodinny",
    "stavba",
    "objekt",
    "rekonstrukcia",
    "ulica",
    "projekt",
    "areal",
    "budova",
    "novostavba",
    "hala",
    "byty",
    "bytov",
}


def match_sites(question: str, sites: list[SiteRef]) -> list[SiteRef]:
    q_tokens = question_tokens(question)
    scored: list[tuple[int, SiteRef]] = []
    for s in sites:
        parts = [p for p in tokens(s.name) if len(p) >= 4 and p not in SITE_GENERIC]
        score = sum(_best(q_tokens, p)[0] >= 2 for p in parts)
        if score:
            scored.append((score, s))
    if not scored:
        return []
    top = max(sc for sc, _ in scored)
    return [s for sc, s in scored if sc == top]


TASK_STOP = {
    "robi",
    "robil",
    "robila",
    "robia",
    "pracuje",
    "pracoval",
    "uloha",
    "ulohu",
    "ulohe",
    "ulohy",
    "ulohach",
    "dnes",
    "zajtra",
    "kto",
    "ktory",
    "ktora",
    "stav",
    "praca",
    "prace",
    "pracu",
    "stavba",
    "stavbe",
    "stavby",
    "termin",
    "terminu",
}


# „pracovníci“ (workers) in the question is not a task name
# („… vypíš PIN všetkých pracovníkov“ = list the PINs of all workers)
PEOPLE_WORDS = ("pracovn", "robotn", "zamestn", "koleg", "ludi")


def _related(a: str, b: str) -> bool:
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n >= max(4, min(len(a), len(b)) - 2)


def match_tasks(question: str, tasks: list[TaskFact], exclude: set[str]) -> list[TaskFact]:
    """Tasks whose name the question mentions („kto robí na murovaní 2. NP“ = who works on the
    masonry of the 2nd floor)."""
    said = [
        t
        for t in tokens(question)
        if len(t) >= 5
        and t not in TASK_STOP
        and t not in exclude
        and not t.startswith(PEOPLE_WORDS)
    ]
    if not said:
        return []
    scored: list[tuple[int, TaskFact]] = []
    for task in tasks:
        title = [t for t in tokens(task.title) if len(t) >= 5 and t not in TASK_STOP]
        score = sum(any(_related(t, s) for s in said) for t in title)
        if score:
            scored.append((score, task))
    if not scored:
        return []
    top = max(sc for sc, _ in scored)
    return [t for sc, t in scored if sc == top]


NAME_RE = re.compile(
    r"\b([A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ][a-záäčďéíĺľňóôŕšťúýž]+)"
    r"(?:\s+([A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ][a-záäčďéíĺľňóôŕšťúýž]+))?"
)


def unknown_person(question: str, known_tokens: set[str]) -> str | None:
    """Two capitalised words next to each other (not at the start of a sentence, not a site/month) —
    looks like the first and last name of a person we do not have on the team."""
    for m in NAME_RE.finditer(question):
        if not m.group(2):
            continue
        before = question[: m.start()].rstrip()
        if not before or before[-1] in ".!?":
            continue
        words = {norm(m.group(1)), norm(m.group(2))}
        if words & known_tokens:
            continue
        return f"{m.group(1)} {m.group(2)}"
    return None

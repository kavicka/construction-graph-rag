"""Speech transcript → structured report (LLM with a JSON schema) + verification of every number.

The LLM may only turn text into structure. Times, hours, minutes and percentages it returns must be
backed by the transcript (sk_numbers) — otherwise they are dropped and the system asks again. Task
status (done / in progress / not worked on) is a category, which we take from the LLM."""

import json
import logging
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.llm.ollama_client import OllamaError, generate_sync
from app.workforce.config import get_workforce_settings
from app.workforce.report_model import ExtraWork, ReportData
from app.workforce.sk_numbers import (
    hours_supported,
    is_short_negative,
    minutes_supported,
    normalize,
    number_candidates,
    parse_hhmm,
    percent_supported,
    time_candidates,
    time_supported,
    to_24h,
)

logger = logging.getLogger(__name__)

Status = Literal["done", "in_progress", "not_started"]
# Bounds for what the LLM writes. They go into the JSON schema only (Ollama enforces maxLength /
# maxItems in its grammar, which cuts off a model that keeps repeating one sentence) and are not
# validated, so a longer reply from a provider without a grammar still parses.
LLM_MAX_TOKENS = 1024


def bounded(limit: int, default: Any = "") -> Any:
    """Field with maxLength (strings) in the LLM schema."""
    return Field(default=default, json_schema_extra={"maxLength": limit})


def bounded_list(limit: int) -> Any:
    """Field with maxItems in the LLM schema."""
    return Field(default_factory=list, json_schema_extra={"maxItems": limit})


Text = Annotated[str, Field(json_schema_extra={"maxLength": 300})]
Short = Annotated[str, Field(json_schema_extra={"maxLength": 20})]


class _XTask(BaseModel):
    ref: Short
    status: Status | None = None
    progress: int | None = None
    hours: float | None = None
    note: str = bounded(300)


class _XExtra(BaseModel):
    description: Text
    hours: float | None = None


class _Extracted(BaseModel):
    absent: bool = False
    absence_reason: str = bounded(300)
    arrival: Short | None = None
    departure: Short | None = None
    break_minutes: int | None = None
    tasks: list[_XTask] = bounded_list(30)
    extra_work: list[_XExtra] = bounded_list(10)
    problems_mentioned: bool = False
    problems: list[Text] = bounded_list(10)
    summary: str = bounded(500)


class _XTaskAnswer(BaseModel):
    status: Status | None = None
    progress: int | None = None
    hours: float | None = None
    note: str = bounded(300)


class _XHoursFix(BaseModel):
    arrival: Short | None = None
    departure: Short | None = None
    break_minutes: int | None = None
    tasks: list[_XTask] = bounded_list(30)
    extra_work: list[_XExtra] = bounded_list(10)


class _XProblems(BaseModel):
    has_problems: bool
    problems: list[Text] = bounded_list(10)


EXTRACT_SYSTEM = """Spracúvaš denný hlasový výpis pracovníka stavebnej firmy. Text je prepis reči —
môže obsahovať chyby prepisu, nárečie a hovorové výrazy. Vráť JSON podľa schémy.

Pravidlá:
- arrival / departure: začiatok a koniec pracovného dňa ako "HH:MM" (24 h), len ak ich povedal.
  „pol ôsmej“ = 07:30, „štvrť na päť“ = 04:15, „tri štvrte na osem“ = 07:45. Koniec práce je
  zvyčajne popoludní („o štvrtej“ = 16:00).
- break_minutes: dĺžka prestávky v minútach, len ak ju povedal („pol hodiny“ = 30,
  „bez prestávky“ = 0).
- tasks: LEN úlohy zo zoznamu, o ktorých hovoril; ref je T-číslo zo zoznamu.
  status: done = hotová / dokončená, in_progress = robil na nej, ale nie je hotová,
  not_started = povedal, že sa na nej dnes nerobilo. progress = percento hotovosti, ak ho
  povedal („na polovicu“ = 50). hours = koľko hodín na nej robil, ak to povedal.
  note = krátko, čo konkrétne robil, alebo prečo sa nerobilo.
- extra_work: práca, ktorá NIE JE v zozname úloh (popis + hodiny, ak ich povedal).
- problems_mentioned: true, ak hovoril o problémoch alebo povedal, že žiadne neboli.
  problems: zoznam konkrétnych problémov a prekážok (počasie, chýbajúci materiál, porucha, úraz).
- absent: true len ak povedal, že dnes vôbec nepracoval (dovolenka, choroba…);
  absence_reason = dôvod.
- summary: 1–2 vety po slovensky, čo dnes robil.
Nikdy si nevymýšľaj časy ani čísla, ktoré v texte nie sú — čo nepovedal, daj null."""


def _llm_json(
    prompt: str, system: str, schema: type[BaseModel], *, max_tokens: int = LLM_MAX_TOKENS
) -> Any:
    try:
        raw = generate_sync(
            get_workforce_settings().effective_llm,
            prompt,
            system=system,
            json_schema=schema.model_json_schema(),
            temperature=0.0,
            num_ctx=8192,
            timeout=240,
            max_tokens=max_tokens,
        )
        return schema.model_validate_json(raw)
    except (OllamaError, ValidationError, json.JSONDecodeError) as exc:
        logger.warning("LLM extrakcia (%s) zlyhala: %s", schema.__name__, exc)
        return None


def task_refs(tasks: list[dict[str, Any]]) -> dict[str, str]:
    return {f"T{i + 1}": t["id"] for i, t in enumerate(tasks)}


def tasks_block(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "(žiadne priradené úlohy)"
    lines = []
    for i, t in enumerate(tasks):
        extra = f" — {t['description'][:160]}" if t.get("description") else ""
        lines.append(f"T{i + 1}: {t['title']} (stavba {t['site_name']}){extra}")
    return "\n".join(lines)


NO_BREAK_RE = re.compile(
    r"bez prestavk|prestavk\w* (nebol\w*|nemal\w*)|(nebol\w*|nemal\w*|ziadn\w*) prestavk"
)


def _break_ok(minutes: int, text: str) -> bool:
    if minutes == 0:
        return bool(NO_BREAK_RE.search(normalize(text))) or is_short_negative(text)
    return minutes_supported(minutes, text)


def _time(value: str | None, text: str, *, afternoon: bool) -> str | None:
    hm = parse_hhmm(value)
    if hm is None:
        return None
    if not time_supported(value, text):
        repaired = _repair_half_hour(hm, text)
        if repaired is None:
            return None
        hm = repaired
    h, m = to_24h(hm, afternoon_hint=afternoon)
    return f"{h:02d}:{m:02d}"


def _repair_half_hour(hm: tuple[int, int], text: str) -> tuple[int, int] | None:
    """The LLM reads „pol siedmej“ (6:30) as 7:30 (an hour later). If the text contains exactly one
    time with the same minutes and an hour 1 lower, that one is used — the value comes from the
    text, not the LLM."""
    h, m = hm
    if m == 0:
        return None
    hits = [(ch, cm) for ch, cm in time_candidates(text) if cm == m and (h - ch) % 12 == 1]
    return hits[0] if len(hits) == 1 else None


def _hours(value: float | None, text: str) -> float | None:
    if value is None or not (0 < value <= 16) or not hours_supported(value, text):
        return None
    return round(value, 2)


def _progress(value: int | None, text: str) -> int | None:
    if value is None or not (0 <= value <= 100) or not percent_supported(value, text):
        return None
    return value


GENERIC_WORDS = {"uloha", "ulohy", "prace", "praca", "stavba", "stavby", "objekt", "treba"}


def _words(text: str) -> set[str]:
    return {
        w
        for w in normalize(text).split()
        if len(w) >= 4 and not w.isdigit() and w not in GENERIC_WORDS
    }


def _related(a: str, b: str) -> bool:
    """The same word stem in another case: rámy ↔ rámov, dosku ↔ dosky, základovú ↔ základovej.
    Common prefix of at least 3 characters and at most 2 differing trailing characters of the
    shorter word."""
    n = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        n += 1
    return n >= max(3, min(len(a), len(b)) - 2)


def task_mentioned(title: str, text: str, description: str = "") -> bool:
    """Did the worker mention the task? A word from the title or description must be spoken in the
    text (in any case). Otherwise the LLM "infers" the status of a task that was never mentioned,
    and the question is skipped."""
    task_words = _words(title) | _words(description[:200])
    if not task_words:
        return True
    said = _words(text)
    return any(_related(t, w) for t in task_words for w in said)


def merge_extraction(
    data: ReportData,
    x: _Extracted,
    text: str,
    refs: dict[str, str],
    titles: dict[str, tuple[str, str]] | None = None,
) -> ReportData:
    """Verified values from the extraction overwrite the report data; unsupported ones are dropped.

    titles (id → title) enables the check that the task was actually mentioned in the text."""
    out = data.model_copy(deep=True)
    if x.absent and x.absence_reason.strip():
        out.absent, out.absence_reason = True, x.absence_reason.strip()[:300]
    if arrival := _time(x.arrival, text, afternoon=False):
        out.arrival = arrival
    if departure := _time(x.departure, text, afternoon=True):
        out.departure = departure
    if (
        x.break_minutes is not None
        and 0 <= x.break_minutes <= 300
        and _break_ok(x.break_minutes, text)
    ):
        out.break_minutes = x.break_minutes

    for xt in x.tasks:
        task_id = refs.get(xt.ref.strip().upper())
        if task_id is None:
            continue
        title, description = (titles or {}).get(task_id, ("", ""))
        if titles is not None and not task_mentioned(title, text, description):
            logger.info("úloha %s v prepise nezaznela — stav od LLM zahodený", task_id)
            continue
        entry = out.upsert_entry(task_id)
        if xt.status:
            entry.status = xt.status
        if entry.status == "done":
            entry.progress = 100
        elif (p := _progress(xt.progress, text)) is not None:
            entry.progress = p
        if (h := _hours(xt.hours, text)) is not None:
            entry.hours = h
        if xt.note.strip():
            entry.note = xt.note.strip()[:500]

    known = {normalize(e.description) for e in out.extra_work}
    for xe in x.extra_work:
        desc = xe.description.strip()[:300]
        if not desc or normalize(desc) in known:
            continue
        out.extra_work.append(ExtraWork(description=desc, hours=_hours(xe.hours, text)))
        known.add(normalize(desc))

    if x.problems_mentioned or x.problems:
        out.problems_answered = True
        for problem in x.problems:
            if problem.strip() and problem.strip() not in out.problems:
                out.problems.append(problem.strip()[:300])
    if summary := clean_summary(x.summary, text):
        out.summary = summary[:1000]
    return out


# end of a sentence only before a capital letter — „nad 4. podlažím“ (above the 4th floor) is not a
# sentence end
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ])")
CLOCK_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")


def clean_summary(summary: str, text: str) -> str:
    """The summary is free LLM text — a sentence with a time or number that was not spoken is
    dropped (the model writes "I started at 7:30" when the person said „o pol siedmej“)."""
    numbers = number_candidates(text)
    kept = []
    for sentence in SENTENCE_RE.split(summary.strip()):
        ok = all(time_supported(t.replace(".", ":"), text) for t in CLOCK_RE.findall(sentence))
        rest = CLOCK_RE.sub(" ", sentence)
        ok = ok and all(
            float(n.replace(",", ".")) in numbers for n in re.findall(r"\d+(?:[.,]\d+)?", rest)
        )
        if ok and sentence.strip():
            kept.append(sentence.strip())
    return " ".join(kept)


def extract_report(
    transcript: str, tasks: list[dict[str, Any]], data: ReportData, day: str
) -> ReportData:
    refs = task_refs(tasks)
    prompt = (
        f"Dátum: {day}\nÚlohy pracovníka na dnes:\n{tasks_block(tasks)}\n\n"
        f'Prepis výpisu:\n"""\n{transcript}\n"""'
    )
    x = _llm_json(prompt, EXTRACT_SYSTEM, _Extracted)
    if x is None:
        x = _Extracted()  # LLM failed — at least the deterministic fallback rules below
    titles = {t["id"]: (t["title"], t.get("description") or "") for t in tasks}
    out = merge_extraction(data, x, transcript, refs, titles)
    if not out.absent and (out.arrival is None or out.departure is None):
        _times_from_text(out, transcript)
    if (
        not out.absent
        and (reason := absence_reason(transcript))
        and not (out.arrival or out.departure or any(e.status for e in out.tasks))
    ):
        out.absent, out.absence_reason = True, reason
    return out


def _times_from_text(data: ReportData, text: str) -> None:
    """The LLM misread a time and validation dropped it — if exactly two times were spoken in the
    text, the earlier is the start and the later the end of the day (the only order that makes
    sense)."""
    cands = sorted(time_candidates(text))
    if len(cands) != 2:
        return
    options = []
    for first, second in (cands, cands[::-1]):
        a, b = to_24h(first, afternoon_hint=False), to_24h(second, afternoon_hint=True)
        if 5 <= a[0] <= 11 and a < b <= (22, 0):
            options.append((f"{a[0]:02d}:{a[1]:02d}", f"{b[0]:02d}:{b[1]:02d}"))
    if len(set(options)) != 1:
        return
    arrival, departure = options[0]
    # an already verified value must agree with the text, otherwise we fill in nothing
    if data.arrival not in (None, arrival) or data.departure not in (None, departure):
        return
    data.arrival, data.departure = arrival, departure


ABSENCE_RE = re.compile(
    r"\b(nebol\w* (som )?(v praci|v robote|na stavbe)|nepracoval\w*|dovolenk\w*|\bpn\b"
    r"|marodk\w*|chor\w*|u lekara|na lekarsk\w*|volno|paragraf\w*)"
)
ABSENCE_LABELS = [
    ("dovolenk", "Dovolenka"),
    ("pn", "Choroba (PN)"),
    ("marod", "Choroba (PN)"),
    ("chor", "Choroba (PN)"),
    ("lekar", "Lekár"),
    ("volno", "Voľno"),
    ("paragraf", "Lekár"),
]


def absence_reason(text: str) -> str | None:
    """Deterministic fallback: „dnes som nebol v práci, mal som dovolenku“ (I was not at work today,
    I had a holiday) → „Dovolenka“."""
    norm = normalize(text)
    if len(norm.split()) > 40 or not ABSENCE_RE.search(norm):
        return None  # a long report with work is not an absence
    return next((label for key, label in ABSENCE_LABELS if key in norm), "Neprítomnosť")


# --- answers to follow-up questions -----------------------------------------------------

DONE_RE = re.compile(r"\b(hotov\w*|dokonc\w*|dorobil\w*|dorobene|skoncen\w*|spraven\w*)\b")
PROGRESS_RE = re.compile(r"\b(rozrob\w*|rozpracov\w*|pokracuj\w*|este nie\w*|nie je hotov\w*)\b")
NOT_STARTED_RE = re.compile(r"\b(nerobil\w*|nerobilo|nezacal\w*|nezacat\w*|nie)\b")


def _status_keywords(text: str) -> Status | None:
    norm = normalize(text)
    if PROGRESS_RE.search(norm):
        return "in_progress"
    if DONE_RE.search(norm):
        return "done"
    if NOT_STARTED_RE.search(norm):
        return "not_started"
    return None


TASK_ANSWER_SYSTEM = """Pracovník stavebnej firmy odpovedá na otázku o jednej úlohe. Vráť JSON:
status: done = hotová, in_progress = robil na nej, ale nie je hotová, not_started = dnes sa na nej
nerobilo; null ak to z odpovede nevyplýva. hours = koľko hodín na nej dnes robil (len ak povedal).
progress = percento hotovosti (len ak povedal, „polovica“ = 50). note = krátko čo robil alebo
prečo sa nerobilo (dôvod). Čo nepovedal, daj null. Čísla si nevymýšľaj."""


def parse_task_answer(
    task: dict[str, Any], question: str, answer: str, detail: str
) -> dict[str, Any]:
    """→ changes for TaskEntry (verified only). Empty dict = not understood."""
    parts = set(detail.split(","))
    cands = sorted(c for c in number_candidates(answer) if c > 0)
    out: dict[str, Any] = {}

    # simple cases without the LLM: „tri“ (three), „päť hodín“ (five hours), „80 percent“
    if parts == {"hours"} and len(cands) == 1 and cands[0] <= 16:
        return {"hours": cands[0]}
    if parts == {"progress"} and len(cands) == 1 and cands[0] <= 100:
        return {"progress": int(cands[0])}
    if parts == {"reason"}:
        text = answer.strip()
        return {"note": text[:500]} if len(normalize(text)) >= 3 else {}

    prompt = (
        f"Úloha: {task['title']} (stavba {task.get('site_name', '')})\n"
        f'Otázka: {question}\nOdpoveď: "{answer}"'
    )
    x = _llm_json(prompt, TASK_ANSWER_SYSTEM, _XTaskAnswer)
    status = (x.status if x else None) or _status_keywords(answer)
    if status:
        out["status"] = status
    if x is not None:
        if (h := _hours(x.hours, answer)) is not None:
            out["hours"] = h
        if (p := _progress(x.progress, answer)) is not None:
            out["progress"] = p
        if x.note.strip():
            out["note"] = x.note.strip()[:500]
    if status == "not_started" and "note" not in out and len(normalize(answer)) > 12:
        out["note"] = answer.strip()[:500]
    # „hotová, robil som tri hodiny“ (done, I worked three hours) — hours without the LLM if the
    # number is the only one
    if (
        "hours" not in out
        and status in ("done", "in_progress")
        and len(cands) == 1
        and cands[0] <= 16
        and hours_supported(cands[0], answer)
    ):
        out["hours"] = cands[0]
    # a bare note („robil som tam niečo“ — I did something there) is not an answer — the question
    # stays and gets sharper
    if not (set(out) - {"note"}) and "reason" not in parts:
        return {}
    return out


HOURS_FIX_SYSTEM = """Pracovníkovi nesedí súčet hodín pri úlohách s pracovným časom. Z jeho odpovede
vráť len opravy, ktoré povedal: tasks = úlohy zo zoznamu (ref) s novým počtom hodín,
extra_work = ďalšia práca mimo zoznamu s hodinami, arrival/departure = opravený čas ("HH:MM"),
break_minutes = opravená prestávka. Čo neopravil, nevracaj. Čísla si nevymýšľaj."""


def parse_hours_answer(
    answer: str, data: ReportData, tasks: list[dict[str, Any]]
) -> ReportData | None:
    refs = task_refs(tasks)
    lines = []
    for i, t in enumerate(tasks):
        entry = data.entry(t["id"])
        lines.append(f"T{i + 1}: {t['title']} — {entry.hours if entry else None} h")
    current = "\n".join(lines)
    prompt = (
        f"Pracovný čas: {data.arrival}–{data.departure}, prestávka {data.break_minutes} min "
        f"= {data.worked_hours} h.\nHodiny pri úlohách:\n{current}\n"
        + "".join(f"Iná práca: {e.description} — {e.hours} h\n" for e in data.extra_work)
        + f'\nOdpoveď pracovníka: "{answer}"'
    )
    x = _llm_json(prompt, HOURS_FIX_SYSTEM, _XHoursFix)
    if x is None:
        return None
    fixed = merge_extraction(
        data,
        _Extracted(
            arrival=x.arrival,
            departure=x.departure,
            break_minutes=x.break_minutes,
            tasks=x.tasks,
            extra_work=x.extra_work,
        ),
        answer,
        refs,
    )
    return None if fixed == data else fixed


PROBLEMS_SYSTEM = """Pracovník odpovedá, či boli dnes na stavbe problémy alebo prekážky. Vráť JSON:
has_problems (bool) a problems = zoznam konkrétnych problémov krátkymi vetami po slovensky."""


def parse_problems_answer(answer: str) -> list[str] | None:
    """→ [] = no problems, list = problems, None = not understood."""
    text = answer.strip()
    if not text:
        return None
    if is_short_negative(text):
        return []
    x = _llm_json(f'Odpoveď: "{text}"', PROBLEMS_SYSTEM, _XProblems)
    if x is None:
        return [text[:300]] if len(normalize(text)) >= 8 else None
    if not x.has_problems:
        return []
    return [p.strip()[:300] for p in x.problems if p.strip()] or [text[:300]]


def parse_time_answer(answer: str, *, afternoon: bool) -> str | None:
    """Answer to „O koľkej…“ (At what time…) — deterministic, no LLM. Must be unambiguous."""
    cands = time_candidates(answer)
    if not cands:
        # „sedem“, „7“ without a preposition
        nums = [c for c in number_candidates(answer) if c == int(c) and 1 <= c <= 24]
        cands = {(int(n) % 12, 0) for n in nums}
    if len(cands) != 1:
        return None
    h, m = to_24h(next(iter(cands)), afternoon_hint=afternoon)
    return f"{h:02d}:{m:02d}"


HALF_HOUR_RE = re.compile(r"\bpol ?hodin\w*|\bpolhodk\w*")
HOUR_AND_HALF_RE = re.compile(r"\b(hodin\w* a pol|poldruha hodin\w*)")


def parse_break_answer(answer: str) -> int | None:
    norm = normalize(answer)
    if NO_BREAK_RE.search(norm) or is_short_negative(answer):
        return 0
    if HOUR_AND_HALF_RE.search(norm):
        return 90
    if HALF_HOUR_RE.search(norm) and not re.search(r"\d", norm):
        return 30
    cands = sorted(number_candidates(answer))
    if len(cands) != 1:
        return None
    value = cands[0]
    is_hours = bool(re.search(r"hodin|hodk", norm)) or value <= 2
    minutes = int(round(value * 60)) if is_hours else int(value)
    return minutes if 0 <= minutes <= 300 else None

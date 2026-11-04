"""Termíny z dokumentov dodávateľov → (od, do). Deterministicky, relatívne k dátumu dokumentu.

LLM z dokumentu len doslovne vytiahne text termínu („do konca mesiaca“, „15.–18. 10.“,
„v 43. týždni“). Prepočet robí tento kód — LLM s kalendárom zle počíta.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.workforce.sk_numbers import CARDINALS, normalize

MONTHS = {
    "januar": 1,
    "februar": 2,
    "marec": 3,
    "marca": 3,
    "april": 4,
    "aprila": 4,
    "maj": 5,
    "maja": 5,
    "jun": 6,
    "juna": 6,
    "jul": 7,
    "jula": 7,
    "august": 8,
    "augusta": 8,
    "september": 9,
    "septembra": 9,
    "oktober": 10,
    "oktobra": 10,
    "november": 11,
    "novembra": 11,
    "december": 12,
    "decembra": 12,
    "januara": 1,
    "februara": 2,
    "januari": 1,
    "februari": 2,
    "marci": 3,
    "aprili": 4,
    "maji": 5,
    "juni": 6,
    "juli": 7,
    "auguste": 8,
    "septembri": 9,
    "oktobri": 10,
    "novembri": 11,
    "decembri": 12,
}
WEEKDAYS = {
    "pondelok": 0,
    "pondelka": 0,
    "pondelku": 0,
    "utorok": 1,
    "utorka": 1,
    "utorku": 1,
    "streda": 2,
    "stredu": 2,
    "stredy": 2,
    "strede": 2,
    "stvrtok": 3,
    "stvrtka": 3,
    "stvrtku": 3,
    "piatok": 4,
    "piatka": 4,
    "piatku": 4,
    "sobota": 5,
    "sobotu": 5,
    "soboty": 5,
    "sobote": 5,
    "nedela": 6,
    "nedelu": 6,
    "nedele": 6,
    "nedeli": 6,
}

DATE_RE = re.compile(r"\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.(?:\s*(\d{2,4}))?")
DATE_RANGE_RE = re.compile(
    r"\b(\d{1,2})\s*\.\s*(?:(\d{1,2})\s*\.\s*)?(?:-|–|az|do)\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.(?:\s*(\d{2,4}))?"
)
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
WEEK_RE = re.compile(
    r"\b(?:kw|tyzden|tyzdni|tyzdna)\s*\.?\s*(\d{1,2})\b"
    r"|\b(\d{1,2})\s*\.?\s*(?:kalendarn\w* )?tyzd\w*"
)
DAY_MONTH_WORD_RE = re.compile(r"\b(\d{1,2})\s*\.?\s*(" + "|".join(MONTHS) + r")\b")


@dataclass
class DateRange:
    date_from: date | None
    date_to: date | None
    how: str  # "absolute" | "relative" | "none"


def _year(raw: str | None, ref: date, month: int, day: int) -> int:
    if raw:
        y = int(raw)
        return y + 2000 if y < 100 else y
    # bez roka: najbližší výskyt od dátumu dokumentu (dokument z decembra → január = nový rok)
    candidate = date(ref.year, month, min(day, calendar.monthrange(ref.year, month)[1]))
    return ref.year + 1 if candidate < ref - timedelta(days=60) else ref.year


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _week_bounds(year: int, week: int) -> tuple[date, date] | None:
    try:
        start = date.fromisocalendar(year, week, 1)
    except ValueError:
        return None
    return start, start + timedelta(days=4)  # pracovný týždeň po–pi


def _number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return CARDINALS.get(token)


def parse_term(text: str | None, ref: date) -> DateRange:
    """Text termínu → rozsah. Chýbajúci začiatok dopĺňa volajúci (dátum dokumentu/dnes)."""
    if not text or not text.strip():
        return DateRange(None, None, "none")
    norm = normalize(text).replace(":", ".")
    raw = text.casefold()

    if m := ISO_RE.search(raw):
        d = _safe(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return _with_direction(norm, d)

    if m := DATE_RANGE_RE.search(raw.replace("až", "az")):
        d1, m1, d2, m2, y = m.groups()
        month1 = int(m1) if m1 else int(m2)
        y2 = _year(y, ref, int(m2), int(d2))
        start = _safe(y2 if month1 <= int(m2) else y2 - 1, month1, int(d1))
        end = _safe(y2, int(m2), int(d2))
        if start and end and start <= end:
            return DateRange(start, end, "absolute")

    dates = []
    for m in DATE_RE.finditer(raw):
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mm <= 12 and (d := _safe(_year(yy, ref, mm, dd), mm, dd)):
            dates.append(d)
    for m in DAY_MONTH_WORD_RE.finditer(norm):
        mm = MONTHS[m.group(2)]
        dd = int(m.group(1))
        if d := _safe(_year(None, ref, mm, dd), mm, dd):
            dates.append(d)
    if len(dates) >= 2:
        return DateRange(min(dates), max(dates), "absolute")
    if len(dates) == 1:
        return _with_direction(norm, dates[0])

    if m := WEEK_RE.search(norm):
        week = int(m.group(1) or m.group(2))
        year = ref.year if week >= ref.isocalendar()[1] - 8 else ref.year + 1
        if bounds := _week_bounds(year, week):
            return DateRange(*bounds, "absolute")

    return _relative(norm, ref)


def _with_direction(norm: str, d: date) -> DateRange:
    """„do 20. 10.“ = koniec, „od 12. 10.“ = začiatok, samotný dátum = jednodňová úloha."""
    if re.search(r"\b(do|najneskor|termin dokoncenia|dokoncit|ukoncit)\b", norm):
        return DateRange(None, d, "absolute")
    if re.search(r"\b(od|zaciatok|zacat|nastup)\b", norm):
        return DateRange(d, None, "absolute")
    return DateRange(d, d, "absolute")


def _relative(norm: str, ref: date) -> DateRange:
    def rng(a: date | None, b: date | None) -> DateRange:
        return DateRange(a, b, "relative")

    if "dnes" in norm:
        return rng(ref, ref)
    if "zajtra" in norm or "zajtrajs" in norm:
        d = ref + timedelta(days=1)
        return rng(d, d)
    if re.search(r"koniec? (tohto )?tyzdna|koncom tyzdna|do konca tyzdna", norm):
        return rng(None, ref + timedelta(days=max(0, 4 - ref.weekday())))
    if re.search(r"(buduci|nasledujuci|dalsi) tyzden", norm):
        start = ref + timedelta(days=7 - ref.weekday())
        return rng(start, start + timedelta(days=4))
    if re.search(r"(buduci|nasledujuci|dalsi) mesiac", norm):
        start = _month_end(ref) + timedelta(days=1)
        return rng(start, _month_end(start))
    if re.search(r"(do )?konca (tohto )?mesiaca|koncom mesiaca", norm):
        return rng(None, _month_end(ref))
    if m := re.search(r"\b(?:o|za|do|behom|pocas)\s+(\w+)\s+(dn\w*|tyzd\w*|mesiac\w*)", norm):
        n = _number(m.group(1))
        if n:
            unit = m.group(2)
            days = n * (7 if unit.startswith("tyzd") else 30 if unit.startswith("mesiac") else 1)
            return rng(None, ref + timedelta(days=days))
    if m := re.search(r"\b(?:v|vo|do|najneskor v|najneskor do)\s+(\w+)\b", norm):
        if (wd := WEEKDAYS.get(m.group(1))) is not None:
            delta = (wd - ref.weekday()) % 7 or 7
            d = ref + timedelta(days=delta)
            return rng(None, d) if "do" in norm.split() else rng(d, d)
        if (month := MONTHS.get(m.group(1))) is not None:
            year = ref.year if month >= ref.month else ref.year + 1
            start = date(year, month, 1)
            return rng(start if month != ref.month else ref, _month_end(start))
    return DateRange(None, None, "none")

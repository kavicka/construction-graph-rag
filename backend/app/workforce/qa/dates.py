"""Deň alebo obdobie z otázky — „dnes“, „v piatok“, „minulý týždeň“, „za september“.

Na rozdiel od `sk_dates.parse_term` (termíny v dokumentoch smerujú do budúcnosti) sa tu
pozerá aj dozadu: „čo robil v piatok“ = posledný piatok, „čo bude robiť v piatok“ = najbližší.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from app.workforce.sk_dates import MONTHS, WEEKDAYS
from app.workforce.sk_numbers import CARDINALS

WEEKDAY_SHORT = ["po", "ut", "st", "št", "pi", "so", "ne"]
WEEKDAY_LONG = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]
MONTH_GEN = ["januára", "februára", "marca", "apríla", "mája", "júna", "júla", "augusta",
             "septembra", "októbra", "novembra", "decembra"]

# minulý čas → deň v týždni sa hľadá dozadu
PAST = re.compile(
    r"\b(robil\w*|bol[aio]?|boli|prisiel|prisl\w*|odisiel|odisl\w*|odpracoval\w*|chybal\w*|"
    r"nahlasil\w*|pracoval\w*|stihol|stihl\w*|spravil\w*|urobil\w*|mal[aio]?|mali|"
    r"odovzdal\w*|minul\w*)\b"
)
FUTURE = re.compile(r"\b(bude\w*|budem|budes|budu|zajtra|pozajtra|buduc\w*|nasledujuc\w*)\b")

DATE_RE = re.compile(r"\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?(?:\s*(\d{4}|\d{2})\b)?")
ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DAY_MONTH_RE = re.compile(r"\b(\d{1,2})\s*\.?\s*(" + "|".join(MONTHS) + r")\b")


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    explicit: bool  # obdobie bolo v otázke (inak predvolené podľa typu otázky)

    @property
    def single_day(self) -> bool:
        return self.start == self.end


def fmt_day(d: date, *, weekday: bool = True, year: bool = False) -> str:
    """„št 8. 10.“ / „8. 10. 2026“."""
    text = f"{d.day}. {d.month}."
    if year:
        text += f" {d.year}"
    return f"{WEEKDAY_SHORT[d.weekday()]} {text}" if weekday else text


def fmt_day_long(d: date, today: date) -> str:
    """„dnes (štvrtok 8. 10.)“, „zajtra (piatok 9. 10.)“, „štvrtok 1. 10.“."""
    base = f"{WEEKDAY_LONG[d.weekday()]} {d.day}. {d.month}."
    if d.year != today.year:
        base += f" {d.year}"
    rel = {0: "dnes", 1: "zajtra", -1: "včera"}.get((d - today).days)
    return f"{rel} ({base})" if rel else base


def fmt_period(p: Period, today: date) -> str:
    if p.single_day:
        return fmt_day_long(p.start, today)
    if p.start.day == 1 and p.end == _month_end(p.start):
        return f"{MONTH_NAMES[p.start.month - 1]} {p.start.year}"
    if p.start.day == 1 and p.start.month == today.month and p.end == today:
        return f"{MONTH_NAMES[p.start.month - 1]} {p.start.year} (do dnes)"
    return f"{fmt_day(p.start, weekday=False)} – {fmt_day(p.end, weekday=False, year=True)}"


MONTH_NAMES = ["január", "február", "marec", "apríl", "máj", "jún", "júl", "august",
               "september", "október", "november", "december"]


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _year_for(month: int, day: int, today: date, past: bool) -> int:
    """Dátum bez roka: pri minulom čase posledný výskyt, inak najbližší (±6 mesiacov)."""
    candidate = _safe(today.year, month, min(day, 28) if month == 2 else day)
    if candidate is None:
        return today.year
    if past and candidate > today:
        return today.year - 1
    if not past and candidate < today - timedelta(days=183):
        return today.year + 1
    return today.year


def _explicit_dates(qn: str, today: date, past: bool) -> list[date]:
    found: list[date] = []
    for m in ISO_RE.finditer(qn):
        if d := _safe(int(m.group(1)), int(m.group(2)), int(m.group(3))):
            found.append(d)
    for m in DATE_RE.finditer(qn):
        dd, mm, yy = int(m.group(1)), int(m.group(2)), m.group(3)
        if not (1 <= mm <= 12 and 1 <= dd <= 31):
            continue
        year = (int(yy) + 2000 if len(yy) == 2 else int(yy)) if yy else _year_for(
            mm, dd, today, past)
        if d := _safe(year, mm, dd):
            found.append(d)
    for m in DAY_MONTH_RE.finditer(qn):
        dd, mm = int(m.group(1)), MONTHS[m.group(2)]
        if d := _safe(_year_for(mm, dd, today, past), mm, dd):
            found.append(d)
    return found


def _number(token: str) -> int | None:
    return int(token) if token.isdigit() else CARDINALS.get(token)


def resolve_period(qn: str, today: date) -> Period | None:
    """`qn` = normalizovaná otázka (bez diakritiky). None = v otázke nie je deň ani obdobie."""
    past = bool(PAST.search(qn)) and not FUTURE.search(qn)

    dates = _explicit_dates(qn, today, past)
    if len(dates) >= 2:
        return Period(min(dates), max(dates), True)
    if len(dates) == 1:
        d = dates[0]
        if re.search(r"\bod\b", qn) and not re.search(r"\bdo\b", qn):
            return Period(d, today, True)
        return Period(d, d, True)

    if m := re.search(r"\bposledn\w* (\w+) (dn\w*|tyzd\w*)", qn):
        n = _number(m.group(1))
        if n:
            days = n * 7 if m.group(2).startswith("tyzd") else n
            return Period(today - timedelta(days=days - 1), today, True)
    if re.search(r"\bposledn\w* tyzd\w*", qn):
        return Period(today - timedelta(days=6), today, True)
    if re.search(r"\bposledn\w* mesiac\w*", qn):
        return Period(today - timedelta(days=29), today, True)

    if re.search(r"\bpredvcer\w*", qn):
        d = today - timedelta(days=2)
        return Period(d, d, True)
    if re.search(r"\bvcer\w*", qn):
        d = today - timedelta(days=1)
        return Period(d, d, True)
    if re.search(r"\bpozajtr\w*", qn):
        d = today + timedelta(days=2)
        return Period(d, d, True)
    if re.search(r"\bzajtr\w*", qn):
        d = today + timedelta(days=1)
        return Period(d, d, True)

    if re.search(r"\b(minul\w*|predosl\w*|minuly) tyzd\w*", qn):
        start = _monday(today) - timedelta(days=7)
        return Period(start, start + timedelta(days=6), True)
    if re.search(r"\b(buduc\w*|nasledujuc\w*|dalsi\w*) tyzd\w*", qn):
        start = _monday(today) + timedelta(days=7)
        return Period(start, start + timedelta(days=6), True)
    if re.search(r"\b(tento|tomto|tohto|aktualn\w*) tyzd\w*|\bcez tyzden\b|\bza tyzden\b", qn):
        start = _monday(today)
        end = today if past or not FUTURE.search(qn) else start + timedelta(days=6)
        return Period(start, max(end, start), True)

    if re.search(r"\b(minul\w*|predosl\w*) mesiac\w*", qn):
        end = today.replace(day=1) - timedelta(days=1)
        return Period(end.replace(day=1), end, True)
    if re.search(r"\b(buduc\w*|nasledujuc\w*|dalsi\w*) mesiac\w*", qn):
        start = _month_end(today) + timedelta(days=1)
        return Period(start, _month_end(start), True)
    if re.search(r"\b(tento|tomto|tohto|aktualn\w*) mesiac\w*|\bza mesiac\b", qn):
        return Period(today.replace(day=1), today, True)

    for word, month in MONTHS.items():
        if re.search(rf"\b(za|v|vo|cez|pocas|mesiac)\s+{word}\b", qn) or re.search(
                rf"\b{word}\b", qn) and len(word) >= 5:
            year = today.year if month <= today.month else today.year - 1
            start = date(year, month, 1)
            end = _month_end(start)
            return Period(start, min(end, today) if end >= today else end, True)

    if re.search(r"\b(dnes\w*|teraz|momentaln\w*|aktualne)\b", qn):
        return Period(today, today, True)

    for word, wd in WEEKDAYS.items():
        if re.search(rf"\b{word}\b", qn):
            delta = wd - today.weekday()
            if re.search(r"\bminul\w*\b", qn):
                d = _monday(today) - timedelta(days=7) + timedelta(days=wd)
            elif past:
                d = today + timedelta(days=delta if delta < 0 else delta - 7)
            else:
                d = today + timedelta(days=delta if delta >= 0 else delta + 7)
            return Period(d, d, True)
    return None


def workdays(start: date, end: date) -> list[date]:
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days

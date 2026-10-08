"""Je to firemná otázka, a akého typu? Pravidlá najprv, LLM len keď pravidlá typ nenájdu.

LLM pri fallbacku vidí LEN otázku a vracia hodnotu z pevného zoznamu (JSON enum) — žiadne
údaje z DB, žiadny voľný text, žiadne SQL.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync
from app.workforce.qa.data import SiteRef, Snapshot, TaskFact, WorkerRef
from app.workforce.qa.dates import Period, resolve_period
from app.workforce.qa.entities import (
    match_sites,
    match_tasks,
    match_workers,
    norm,
    tokens,
    unknown_person,
)

logger = logging.getLogger(__name__)

Intent = Literal[
    "worker_day", "worker_tasks", "attendance", "hours", "absences", "missing_reports",
    "site_overview", "task_people", "overdue", "blocked", "problems",
]
INTENTS: tuple[Intent, ...] = (
    "missing_reports", "attendance", "hours", "absences", "worker_day", "worker_tasks",
    "task_people", "site_overview", "overdue", "blocked", "problems",
)

DOC_MARKER = re.compile(
    r"\b(podla|v|vo|z|zo|na zaklade) (zmluv|dokument|pdf|protokol|zapis|harmonogram|norm|"
    r"technick|rozpoct|ponuk|faktur|projekt|vykres|specifikac|smernic|priloh|e ?mail)\w*"
    r"|\bdokument\w*|\bzmluv\w*|\bpdf\b|\bcitac\w*|\bna (ktorej |akej )?stran\w*"
)
STRONG = re.compile(
    r"\bdochadz\w*|\bodpracova\w*|\bprichod\w*|\bodchod\w*|\bprestavk\w*|"
    r"\bpracovni\w*|\brobotni\w*|\bzamestnan\w*|\bkoleg\w*|\bneodovzd\w*|\buloh\w*|"
    r"\bpridelen\w*|\bkolko hodin\b|"
    r"\bkto (dnes |zajtra |teraz |este |vcera |tento tyzden )?(robi|pracuje|robil\w*|"
    r"pracoval\w*|bude robit|bude pracovat|chybal\w*|neprisiel|neprisl\w*|je na|bol na|"
    r"ma na starosti|meska)\b|"
    r"\bco (dnes |zajtra |teraz |vcera |pozajtra )?(robi|robil\w*|bude robit|ma robit|"
    r"mam robit|robim|robia)\b|"
    r"\b(po termine|meskaj\w*|oneskoren\w*|omeskan\w*)\b|\bcaka\w* na\b|\bblokovan\w*|"
    r"\b(prisiel|prisla|prisli|odisiel|odisla|chybal\w*|neprisiel|neprisl\w*|"
    r"nepracoval\w*)\b|\bnebol\w* v praci\b|\bnahlasil\w*|\bnahlasen\w*|"
    r"\bkto (dnes |este |zajtra )?(chyba|chybaju)\b"
)
# „výpis“ (denný výpis) vs. „vypíš“ (sloveso) — bez diakritiky sú rovnaké, preto sa podstatné
# meno hľadá v pôvodnom texte s „ý“ alebo v spojení, kde sloveso nedáva zmysel
REPORT_NOUN = re.compile(r"\bvýpis\w*|\bvypis(y|u|om|och|ov)\b")
REPORT_NOUN_NORM = re.compile(
    r"\b(denn\w*|dnesn\w*|neodovzd\w*|odovzd\w*|bez|nema\w*|chyba\w*) vypis\w*")
WEAK = re.compile(
    r"\bproblem\w*|\bhodin\w*|\bstav\w*|\btermin\w*|\bhotov\w*|\brozpracovan\w*|"
    r"\bpostup\w*|\bco sa (robi|deje)|\bako (to )?(ide|vyzera|pokracuje)|\brobi\w*|"
    r"\bpracuj\w*|\bkde\b|\bprehlad\w*|\bmesk\w*|\bdokedy\b"
)
# stavba sama o sebe je aj téma dokumentov (zmluva, termín dokončenia) — firemná otázka je
# to len so slovom o práci na nej
SITE_WEAK = re.compile(
    r"\brobi\w*|\bpracuj\w*|\bco sa (robi|deje|robilo)|\bako (to )?(ide|vyzera|pokracuje)|"
    r"\bstav (prac|stavby|uloh)|\bprehlad\w*|\bhotov\w*|\brozpracovan\w*|\bmesk\w*|"
    r"\bkto (dnes |zajtra |teraz )?(robi|pracuje|je na|bude|robil|pracoval)\b"
)
SELF_REF = re.compile(
    r"\b(ja|mna|mne|mnou|moj\w*|mam|robim|som|budem|pracujem|mojich|moje)\b"
)

RULES: list[tuple[Intent, re.Pattern[str]]] = [
    ("missing_reports", re.compile(
        r"\bneodovzd\w*|\b(bez|nema\w*|chyba\w*) (denn\w* |dnesn\w* )?vypis\w*|"
        r"\bkto (este )?nema\w* (denn\w* |dnesn\w* )?vypis")),
    ("problems", re.compile(
        r"\bproblem\w*|\bnahlasil\w*|\bnahlasen\w*|\bkomplikac\w*|\bzavad\w*|\bprekazk\w*")),
    ("hours", re.compile(
        r"\bkolko hodin\b|\bodpracova\w*|\bodrobil\w*|\bnadcas\w*|\bhodin\w* (spolu|celkom)")),
    ("absences", re.compile(
        r"\bchybal\w*|\bkto (dnes |este |zajtra )?(chyba|chybaju)\b|\babsenc\w*|"
        r"\bneprisiel\b|\bneprisl\w*|\bnebol\w* v praci|"
        r"\bnepracoval\w*|\bdovolenk\w*|\bpn\b|\bpracovn\w* neschop\w*|\bneprit\w*")),
    ("attendance", re.compile(
        r"\bdochadz\w*|\bprichod\w*|\bodchod\w*|\bprestavk\w*|"
        r"\b(kedy|o kolkej) (dnes |vcera )?(prisiel|prisla|prisli|odisiel|odisla|zacal\w*|"
        r"skoncil\w*)\b|\b(prisiel|prisla|prisli|odisiel|odisla)\b")),
    ("overdue", re.compile(
        r"\bpo termine\b|\bmeska\w*|\bmeskaj\w*|\boneskor\w*|\bomeskan\w*|\bnestiha\w*|"
        r"\bzaostava\w*")),
    ("blocked", re.compile(
        r"\bcaka\w* na\b|\bblokovan\w*|\bzavisl\w*|\bnemoze (sa )?zacat")),
    ("task_people", re.compile(
        r"\bkto (dnes |zajtra |teraz |vsetko )?(robi|pracuje|ma na starosti|je pridelen\w*|"
        r"robil|pracoval) (na|s|pri)\b|\bs kym\b|\bkto (je|su) (na|pri) (ulohe|ulohu)")),
    ("worker_tasks", re.compile(
        r"\buloh\w*|\bpridelen\w*|\bma na starosti\b|\btermin\w*|\bdokedy\b")),
    ("worker_day", re.compile(
        r"\bco (dnes |zajtra |teraz |vcera |pozajtra |v \w+ |cez \w+ )?(robi|robil\w*|"
        r"bude robit|ma robit|mam robit|robim|robia|robit)\b|\bkde (je|bude|robi|pracuje|"
        r"robil|pracoval)\b|\bna com (robi|pracuje|robil)")),
    ("site_overview", re.compile(
        r"\bkto (dnes |zajtra |teraz |vcera )?(robi|pracuje|je na|bude|robil|pracoval)\b|"
        r"\bco sa (robi|deje|bude robit|robilo)\b|\bako (to )?(ide|vyzera|pokracuje)\b|"
        r"\bstav\w* (stavby|stavieb|prac)\b|\bprehlad\w*")),
]
SPECIFIC = {"overdue", "blocked", "task_people", "problems", "hours", "absences",
            "attendance", "missing_reports"}


@dataclass
class FirmaQuery:
    intents: list[Intent]
    workers: list[WorkerRef] = field(default_factory=list)
    sites: list[SiteRef] = field(default_factory=list)
    tasks: list[TaskFact] = field(default_factory=list)
    period: Period | None = None
    self_ref: bool = False
    ambiguous: list[tuple[str, list[WorkerRef]]] = field(default_factory=list)
    unknown_name: str | None = None
    via_llm: bool = False


def _rule_intents(qn: str, *, has_worker: bool, has_tasks: bool,
                  has_sites: bool) -> list[Intent]:
    found: list[Intent] = [intent for intent, rx in RULES if rx.search(qn)]
    if found and any(i in SPECIFIC for i in found):
        # „ktoré úlohy meškajú“ — slovo úloha je tu len podmet, typ určuje „meškajú“
        if {"overdue", "blocked", "task_people"} & set(found):
            found = [i for i in found if i not in ("worker_tasks", "worker_day")]
        found = [i for i in found if i != "site_overview"]
    if "task_people" in found and not has_tasks:
        # „s kým robím?“ bez konkrétnej úlohy = moje úlohy aj s kolegami
        swap: Intent = "worker_tasks" if has_worker else "site_overview"
        found = [i if i != "task_people" else swap for i in found]
    if not has_worker and "site_overview" in found and "worker_day" in found:
        found = [i for i in found if i != "worker_day"]
    if len(found) > 1 and "site_overview" in found:
        found = [i for i in found if i != "site_overview"]
    unique: list[Intent] = list(dict.fromkeys(found))
    if not unique:
        if has_worker:
            unique = ["worker_day"]
        elif has_tasks:
            unique = ["task_people"]
        elif has_sites:
            unique = ["site_overview"]
    return [i for i in INTENTS if i in unique][:2]


class _LlmIntent(BaseModel):
    intents: list[Intent] = Field(default_factory=list, max_length=2)


INTENT_PROMPT = """Zaraď otázku na firemné údaje stavebnej firmy do 1–2 typov z pevného zoznamu.
worker_day = čo robí / robil pracovník v daný deň
worker_tasks = aké úlohy má pracovník pridelené, termíny úloh
attendance = príchod, odchod, prestávka, dochádzka v daný deň
hours = koľko hodín odpracoval za obdobie
absences = kto chýbal, dovolenka, PN, neprítomnosť
missing_reports = kto neodovzdal denný výpis
site_overview = prehľad stavby, kto kde dnes pracuje
task_people = kto robí na konkrétnej úlohe
overdue = úlohy po termíne, omeškanie
blocked = úlohy, ktoré čakajú na inú úlohu
problems = problémy nahlásené vo výpisoch
Ak otázka nepatrí ani do jedného, vráť prázdny zoznam.

Otázka: {question}"""


def llm_intents(question: str) -> list[Intent]:
    try:
        raw = generate_sync(get_settings().answer_model,
                            INTENT_PROMPT.format(question=question[:500]),
                            json_schema=_LlmIntent.model_json_schema(), num_ctx=2048,
                            temperature=0.0, timeout=30.0)
        parsed = _LlmIntent.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("firma: LLM typ otázky zlyhal: %s", exc)
        return []
    return [i for i in INTENTS if i in parsed.intents][:2]


def detect(question: str, snap: Snapshot, today: date, *,
           worker_context: bool = False) -> FirmaQuery | None:
    """None = nie je to otázka na firemné údaje (pokračuje do dokumentov).

    `worker_context` — mobil pracovníka: firemné údaje sú jediná doména, takže sa skúša
    každá otázka a „ja / moje“ znamená prihláseného pracovníka."""
    qn = norm(question)
    resolved = match_workers(question, snap.workers)
    sites = match_sites(question, snap.sites)
    exclude = resolved.name_tokens | {t for s in sites for t in tokens(s.name)}
    tasks = match_tasks(question, snap.tasks, exclude)
    period = resolve_period(qn, today)
    strong = bool(STRONG.search(qn) or REPORT_NOUN.search(question.casefold())
                  or REPORT_NOUN_NORM.search(qn))
    weak = bool(WEAK.search(qn))
    self_ref = bool(SELF_REF.search(qn))
    has_worker = bool(resolved.workers or resolved.ambiguous)

    if has_worker:
        if not (strong or weak or period):
            return None  # „Aký telefón má Mikuš?“ — to je otázka na dokumenty
    elif worker_context:
        if not (strong or weak or period or self_ref) or (DOC_MARKER.search(qn)
                                                           and not self_ref):
            return None  # „Aká je cena betónu?“, „termín podľa zmluvy“ — len nápoveda
    elif DOC_MARKER.search(qn) or not (strong or (sites and SITE_WEAK.search(qn))):
        return None

    intents = _rule_intents(qn, has_worker=has_worker or (worker_context and self_ref),
                            has_tasks=bool(tasks), has_sites=bool(sites))
    query = FirmaQuery(
        intents=intents, workers=resolved.workers, sites=sites, tasks=tasks, period=period,
        self_ref=self_ref, ambiguous=resolved.ambiguous,
    )
    if not intents:
        query.intents = llm_intents(question)
        query.via_llm = True
    if not resolved.workers and not resolved.ambiguous:
        known = {t for w in snap.workers for t in tokens(w.name)} | exclude
        query.unknown_name = unknown_person(question, known)
    return query

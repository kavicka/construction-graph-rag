"""Company question → answer: entities → date → type → access → read-only data → template."""

from dataclasses import dataclass, field
from datetime import date

from app.retrieve.secrets import redact_secrets
from app.workforce.qa.access import Denied, Viewer, authorize
from app.workforce.qa.answer import STEP_LABELS, Ctx, render
from app.workforce.qa.data import snapshot
from app.workforce.qa.dates import fmt_period
from app.workforce.qa.intents import FirmaQuery, detect

HELP_ADMIN = (
    "Z firemných údajov viem povedať, čo kto robí v daný deň, aké má pridelené úlohy, "
    "dochádzku a odpracované hodiny, neprítomnosť, kto neodovzdal výpis, prehľad stavby, "
    "úlohy po termíne alebo čakajúce na inú úlohu a nahlásené problémy. Skús napríklad: "
    "„Čo robí dnes Jozef?“, „Koľko hodín odpracoval Peter tento mesiac?“ alebo "
    "„Ktoré úlohy meškajú?“"
)
HELP_WORKER = (
    "Tu sa môžeš pýtať len na svoje úlohy, dochádzku a hodiny. Skús napríklad: "
    "„Čo mám dnes robiť?“, „Koľko hodín mám tento mesiac?“ alebo „Ktoré moje úlohy meškajú?“"
)

PERSON_INTENTS = {"worker_day", "worker_tasks", "attendance", "hours", "absences", "problems"}


@dataclass
class FirmaResult:
    answer: str
    steps: list[dict[str, str]] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    denied: bool = False
    # False = no site, worker, period or "me" and no type pattern (the type is only guessed) —
    # the chat then prefers a matching document (orchestrator); the phone ignores it
    anchored: bool = True


def _anchored(query: FirmaQuery) -> bool:
    """A task title matched by one word („betonáži“ ~ „Betonáž stropu“) is no anchor."""
    named = query.workers or query.ambiguous or query.sites
    return bool(named or query.period or query.self_ref or query.by_rule)


def _summary(query: FirmaQuery, today: date) -> str:
    bits = [STEP_LABELS[i] for i in query.intents]
    bits += [w.name for w in query.workers] + [s.name for s in query.sites]
    bits += [t.title for t in query.tasks[:2]]
    if query.period:
        bits.append(fmt_period(query.period, today))
    return " · ".join(bits)


def answer_firma(
    question: str, viewer: Viewer, *, today: date | None = None, worker_context: bool = False
) -> FirmaResult | None:
    """None = the question is not about company data (the chat continues to documents). On mobile
    (`worker_context`) an answer is always returned — at least a hint."""
    today = today or date.today()
    snap = snapshot()
    query = detect(question, snap, today, worker_context=worker_context)
    if query is None:
        return FirmaResult(HELP_WORKER) if worker_context else None

    anchored = _anchored(query)
    decision = authorize(query, viewer, snap)
    if isinstance(decision, Denied):
        return FirmaResult(
            decision.message,
            denied=True,
            steps=[{"tool": "firma", "summary": "prístup zamietnutý"}],
            anchored=anchored,
        )
    query = decision

    if query.ambiguous:
        _, candidates = query.ambiguous[0]
        options = " alebo ".join(w.name for w in candidates)
        return FirmaResult(f"Myslíš {options}? Napíš prosím celé meno.")
    # „Koľko hodín odpracoval Ján Novák?“ (How many hours did Ján Novák work?) —
    # without this check the whole team's hours would come back
    if query.unknown_name and not query.workers and PERSON_INTENTS & set(query.intents):
        team = ", ".join(w.name for w in snap.workers if w.active)
        return FirmaResult(
            f"Pracovníka „{query.unknown_name}“ v tíme nemám."
            + (f" V tíme sú: {team}." if team else ""),
            anchored=anchored,
        )
    if not query.intents:
        return FirmaResult(HELP_WORKER if viewer.own_only else HELP_ADMIN, anchored=anchored)

    text = render(Ctx(query=query, snap=snap, viewer=viewer, today=today))
    step = {"tool": "firma", "summary": _summary(query, today)}
    if query.via_llm:
        step["summary"] += " (typ určil model)"
    # task names, notes and problems are written by people — a password can show up there too
    return FirmaResult(
        redact_secrets(text), steps=[step], intents=list(query.intents), anchored=anchored
    )

"""Company data in chat: names in cases, dates, question types, templates, access (§17)."""

import json
import sqlite3
from dataclasses import fields
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.workforce import store
from app.workforce.qa import data as qa_data
from app.workforce.qa.access import MEMBER_DENIED, OTHERS_DENIED, Viewer
from app.workforce.qa.data import WorkerRef
from app.workforce.qa.dates import resolve_period
from app.workforce.qa.engine import HELP_WORKER, answer_firma
from app.workforce.qa.entities import match_workers, norm
from app.workforce.qa.intents import detect
from tests.wf_fixtures import make_site, make_task, make_worker

T = date.today()
ADMIN = Viewer("admin", None)
MEMBER = Viewer("member", "u-member")


def iso(d: date) -> str:
    return d.isoformat()


def submit_report(user_id: str, day: date, **data: Any) -> None:
    report = store.get_or_create_report(user_id, iso(day))
    store.update_report(
        report["id"],
        data_json=json.dumps(data),
        status="submitted",
        submitted_at=f"{iso(day)} 16:00:00",
    )


@pytest.fixture
def firma(client: TestClient) -> dict[str, Any]:
    mikus = make_worker(client, "Jozef Mikuš", professions=["murár"])
    horak = make_worker(client, "Peter Horák")
    kral = make_worker(client, "Jozef Kráľ")
    maly = make_worker(client, "Marek Malý")
    rov = make_site(client, "Rovinka")
    agat = make_site(client, "Bytový dom Agátová")
    betonaz = make_task(
        client,
        agat,
        "Betonáž stropu nad 1. NP",
        [kral],
        date_from=iso(T - timedelta(days=5)),
        date_to=iso(T - timedelta(days=3)),
        status="in_progress",
        progress=60,
    )
    murovanie = make_task(
        client,
        rov,
        "Murovanie obvodových stien 2. NP",
        [mikus, horak],
        date_from=iso(T - timedelta(days=2)),
        date_to=iso(T + timedelta(days=4)),
        status="in_progress",
        progress=40,
    )
    priecky = make_task(
        client,
        agat,
        "Murovanie priečok 2. NP",
        [maly],
        depends_on=betonaz["id"],
        date_to=iso(T + timedelta(days=10)),
    )
    fasada = make_task(
        client, rov, "Zateplenie fasády", [horak], date_from=iso(T + timedelta(days=7))
    )
    submit_report(
        mikus,
        T - timedelta(days=1),
        arrival="06:30",
        departure="15:00",
        break_minutes=30,
        tasks=[{"task_id": murovanie["id"], "status": "in_progress", "progress": 35, "hours": 8}],
        problems=["chýba malta", "heslo k bráne: Brana-7731"],
    )
    submit_report(
        mikus,
        T,
        arrival="07:00",
        departure="15:30",
        break_minutes=30,
        tasks=[{"task_id": murovanie["id"], "status": "in_progress", "progress": 40, "hours": 8}],
    )
    submit_report(horak, T, absent=True, absence_reason="PN")
    return {
        "mikus": mikus,
        "horak": horak,
        "kral": kral,
        "maly": maly,
        "rov": rov,
        "agat": agat,
        "betonaz": betonaz,
        "murovanie": murovanie,
        "priecky": priecky,
        "fasada": fasada,
    }


def ask(question: str, viewer: Viewer = ADMIN, **kw: Any) -> str:
    result = answer_firma(question, viewer, today=T, **kw)
    assert result is not None, question
    return result.answer


# --- read-only data and column whitelist ------------------------------------------------


FORBIDDEN = (
    "pin_hash",
    "password_hash",
    "email",
    "transcript",
    "sessions",
    "api_tokens",
    "token",
    "*",
)


@pytest.mark.parametrize("sql", qa_data.ALL_SQL)
def test_sql_never_reads_sensitive_columns(sql: str) -> None:
    lowered = sql.casefold()
    for word in FORBIDDEN:
        assert word not in lowered, (word, sql)


def test_dto_fields_whitelisted() -> None:
    allowed = {
        "id",
        "name",
        "active",
        "site_id",
        "site_name",
        "title",
        "description",
        "status",
        "progress",
        "date_from",
        "date_to",
        "done_on",
        "depends_on",
        "assignees",
        "user_id",
        "day",
        "submitted_at",
        "data",
    }
    for dto in (qa_data.WorkerRef, qa_data.SiteRef, qa_data.TaskFact, qa_data.ReportFact):
        assert {f.name for f in fields(dto)} <= allowed, dto


def test_connection_is_read_only(firma: dict[str, Any]) -> None:
    with qa_data._ro_conn() as conn:
        assert conn is not None
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE wf_tasks SET title = 'x'")
    assert {w.name for w in qa_data.workers()} >= {"Jozef Mikuš", "Peter Horák"}


# --- names, dates, question types -------------------------------------------------------

TEAM = [
    WorkerRef("m", "Jozef Mikuš", True),
    WorkerRef("h", "Peter Horák", True),
    WorkerRef("k", "Jozef Kráľ", True),
    WorkerRef("y", "Marek Malý", True),
    WorkerRef("j", "Ján Novotný", True),
]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Čo robí dnes Jozef Mikuš?", {"m"}),
        ("co robi dnes jozef mikus", {"m"}),
        ("Koľko hodín odpracoval Mikuš?", {"m"}),
        ("Aké úlohy má Mikušovi pridelené?", {"m"}),
        ("Kedy prišiel Petra Horáka kolega?", {"h"}),
        ("Čo robí Marek?", {"y"}),
        ("Úlohy pre Novotného", {"j"}),
        ("Kto chýbal v januári?", set()),
        ("Je to malý problém na stavbe?", set()),
    ],
)
def test_worker_names_in_cases(question: str, expected: set[str]) -> None:
    assert {w.id for w in match_workers(question, TEAM).workers} == expected


def test_first_name_with_other_surname_is_someone_else() -> None:
    # Ján Novotný is on the team, Ján Novák is not — must not get Novotný's data
    assert not match_workers("Koľko hodín má Ján Novák?", TEAM).workers
    assert {w.id for w in match_workers("Koľko hodín má Ján?", TEAM).workers} == {"j"}


def test_two_jozefs_are_ambiguous() -> None:
    res = match_workers("Čo robí dnes Jozef?", TEAM)
    assert not res.workers
    assert {w.id for w in res.ambiguous[0][1]} == {"m", "k"}


THU = date(2026, 10, 8)  # Thursday


@pytest.mark.parametrize(
    ("question", "start", "end"),
    [
        ("čo robí dnes", THU, THU),
        ("čo robil včera", date(2026, 10, 7), date(2026, 10, 7)),
        ("čo bude robiť zajtra", date(2026, 10, 9), date(2026, 10, 9)),
        ("čo bude robiť v piatok", date(2026, 10, 9), date(2026, 10, 9)),
        ("čo robil v pondelok", date(2026, 10, 5), date(2026, 10, 5)),
        ("koľko hodín minulý týždeň", date(2026, 9, 28), date(2026, 10, 4)),
        ("koľko hodín tento mesiac", date(2026, 10, 1), THU),
        ("koľko hodín za september", date(2026, 9, 1), date(2026, 9, 30)),
        ("čo robil 6. 10.", date(2026, 10, 6), date(2026, 10, 6)),
        ("hodiny od 1. 10. do 7. 10.", date(2026, 10, 1), date(2026, 10, 7)),
        ("problémy za posledných 5 dní", date(2026, 10, 4), THU),
    ],
)
def test_periods(question: str, start: date, end: date) -> None:
    period = resolve_period(norm(question), THU)
    assert period is not None and (period.start, period.end) == (start, end)


@pytest.mark.parametrize(
    ("question", "intents"),
    [
        ("čo robi dnes Jozef Mikus? ake ma pridelene ulohy", ["worker_day", "worker_tasks"]),
        ("Ktoré úlohy meškajú?", ["overdue"]),
        ("Kto ešte neodovzdal výpis?", ["missing_reports"]),
        ("Koľko hodín odpracoval Mikuš tento mesiac?", ["hours"]),
        ("Kto dnes pracuje na Rovinke?", ["site_overview"]),
        ("Kto robí na murovaní obvodových stien?", ["task_people"]),
        ("Čo čaká na betonáž stropu?", ["blocked"]),
        ("Aké problémy nahlásili tento týždeň?", ["problems"]),
        ("Kedy dnes prišiel Mikuš?", ["attendance"]),
        ("Kto chýbal minulý týždeň?", ["absences"]),
    ],
)
def test_intents(firma: dict[str, Any], question: str, intents: list[str]) -> None:
    query = detect(question, qa_data.snapshot(), T)
    assert query is not None and query.intents == intents and not query.via_llm


@pytest.mark.parametrize(
    "question",
    [
        "Aký je termín dokončenia podľa zmluvy?",
        "Aký telefón má Mikuš?",
        "Aký je termín dokončenia stavby Rovinka?",
        "Aké úlohy má zhotoviteľ podľa zmluvy o dielo?",
        "Kto je stavbyvedúci na Agátovej?",
    ],
)
def test_document_questions_stay_in_rag(firma: dict[str, Any], question: str) -> None:
    assert detect(question, qa_data.snapshot(), T) is None


# --- answers (admin) --------------------------------------------------------------------


def test_the_reported_question(firma: dict[str, Any]) -> None:
    text = ask("čo robi dnes Jozef Mikus? ake ma pridelene ulohy")
    assert "Jozef Mikuš" in text
    assert "Murovanie obvodových stien 2. NP" in text and "Rovinka" in text
    assert "rozpracovaná 40 %" in text and "spolu s Peter Horák" in text
    assert "7:00 – 15:30" in text and "odpracované 8 h" in text
    assert "pridelené úlohy" in text


def test_hours_and_attendance(firma: dict[str, Any]) -> None:
    text = ask("Koľko hodín odpracoval Mikuš za posledné 2 dni?")
    assert "spolu **16 h**" in text and "z 2 odovzdaných výpisov" in text
    att = ask("Aká je dnes dochádzka?")
    assert "**Jozef Mikuš**: 7:00 – 15:30, prestávka 30 min, odpracované 8 h" in att
    assert "**Peter Horák**: neprítomnosť — PN" in att
    assert "**Jozef Kráľ**: výpis chýba" in att


def test_missing_overdue_blocked_problems(firma: dict[str, Any]) -> None:
    missing = ask("Kto ešte neodovzdal výpis?")
    assert "Jozef Kráľ" in missing and "Marek Malý" in missing and "Mikuš" not in missing
    late = ask("Ktoré úlohy meškajú?")
    assert "Betonáž stropu nad 1. NP" in late and "po termíne 3 dni" in late
    assert "Murovanie obvodových" not in late
    blocked = ask("Čo čaká na betonáž stropu?")
    assert "**Murovanie priečok 2. NP** (Bytový dom Agátová) čaká na **Betonáž" in blocked
    problems = ask("Aké problémy nahlásili tento týždeň?")
    assert "chýba malta" in problems
    assert "Brana-7731" not in problems and "••••••" in problems


def test_site_and_task_people(firma: dict[str, Any]) -> None:
    site = ask("Kto dnes pracuje na Rovinke?")
    assert "Jozef Mikuš (Murovanie obvodových stien 2. NP)" in site
    assert "Zateplenie fasády" in site  # open, but not yet started
    people = ask("Kto robí na murovaní obvodových stien?")
    assert "Jozef Mikuš, Peter Horák" in people


def test_ambiguous_and_unknown(firma: dict[str, Any]) -> None:
    assert "Myslíš Jozef Kráľ alebo Jozef Mikuš?" in ask("Čo robí dnes Jozef?")
    unknown = ask("Koľko hodín odpracoval Ján Novák tento mesiac?")
    assert "Pracovníka „Ján Novák“ v tíme nemám" in unknown


# --- access -----------------------------------------------------------------------------


def test_member_sees_nothing(firma: dict[str, Any]) -> None:
    assert ask("Čo robí dnes Jozef Mikuš?", MEMBER) == MEMBER_DENIED


def test_worker_sees_only_self(firma: dict[str, Any]) -> None:
    me = Viewer("worker", firma["mikus"])
    own = ask("Čo mám dnes robiť?", me, worker_context=True)
    assert "Murovanie obvodových stien 2. NP" in own and "spolu s Peter Horák" in own
    assert "Betonáž" not in own

    for question in (
        "Čo robí dnes Peter Horák?",
        "Koľko hodín odpracoval Kráľ?",
        "Kto ešte neodovzdal výpis?",
        "Čo robí Ján Novák?",
    ):
        assert ask(question, me, worker_context=True) == OTHERS_DENIED, question

    hours = ask("Koľko hodín mám tento týždeň?", me, worker_context=True)
    assert "Jozef Mikuš" in hours and "Horák" not in hours

    att = ask("Aká je dnes dochádzka?", me, worker_context=True)
    assert "Jozef Mikuš" in att and "PN" not in att and "Kráľ" not in att

    late = ask("Ktoré úlohy meškajú?", me, worker_context=True)
    assert "Betonáž" not in late  # someone else's overdue task

    # someone else's task behaves as if the question hadn't mentioned it
    other = ask("Kto robí na betonáži stropu?", me, worker_context=True)
    assert "Kráľ" not in other and "Betonáž" not in other

    assert ask("Aká je cena betónu?", me, worker_context=True) == HELP_WORKER
    team = ask("S kým robím na mojich úlohách?", me, worker_context=True)
    assert "pridelené úlohy" in team and "spolu s Peter Horák" in team


def test_worker_sees_coworker_names_only(firma: dict[str, Any]) -> None:
    horak = Viewer("worker", firma["horak"])
    text = ask("Aké mám úlohy?", horak, worker_context=True)
    assert "spolu s Jozef Mikuš" in text
    assert "7:00" not in text and "15:30" not in text  # Mikuš's attendance is not

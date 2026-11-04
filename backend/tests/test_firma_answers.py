"""Firma answer templates (app.workforce.qa.answer) as pure functions over an in-memory
snapshot: no DB, no LLM. Every number and date in the text must come from the facts."""

from collections.abc import Callable
from datetime import date, timedelta

import pytest

from app.workforce.qa import answer
from app.workforce.qa.access import Viewer
from app.workforce.qa.answer import (
    Ctx,
    attendance_text,
    blocker,
    days_late,
    hhmm,
    hours,
    markdown_blocks,
    names,
    planned_on,
    status_label,
    task_line,
    term,
)
from app.workforce.qa.data import ReportFact, SiteRef, Snapshot, TaskFact, WorkerRef
from app.workforce.qa.dates import Period, fmt_day, fmt_day_long
from app.workforce.qa.intents import FirmaQuery, Intent
from app.workforce.report_model import ExtraWork, ReportData, TaskEntry

TODAY = date(2026, 10, 8)  # Thursday
ADMIN = Viewer("admin", None)

MIKUS = WorkerRef("w1", "Jozef Mikuš", True)
HORAK = WorkerRef("w2", "Peter Horák", True)
MALY = WorkerRef("w3", "Marek Malý", False)
ROVINKA = SiteRef("s1", "Rovinka", True)
AGATOVA = SiteRef("s2", "Agátová", True)


def iso(d: date) -> str:
    return d.isoformat()


def _task(
    tid: str,
    title: str,
    site: SiteRef,
    status: str = "todo",
    *,
    progress: int = 0,
    date_from: date | None = None,
    date_to: date | None = None,
    done_on: date | None = None,
    depends_on: str | None = None,
    assignees: tuple[str, ...] = (),
) -> TaskFact:
    return TaskFact(
        id=tid,
        site_id=site.id,
        site_name=site.name,
        title=title,
        description="",
        status=status,
        progress=progress,
        date_from=iso(date_from) if date_from else None,
        date_to=iso(date_to) if date_to else None,
        done_on=iso(done_on) if done_on else None,
        depends_on=depends_on,
        assignees=assignees,
    )


BETON = _task(
    "t1",
    "Betonáž stropu",
    ROVINKA,
    "in_progress",
    progress=60,
    date_from=TODAY - timedelta(days=5),
    date_to=TODAY - timedelta(days=3),
    assignees=("w1", "w2"),
)
MUROVANIE = _task(
    "t2",
    "Murovanie",
    ROVINKA,
    "todo",
    date_from=TODAY - timedelta(days=2),
    date_to=TODAY + timedelta(days=5),
    depends_on="t1",
    assignees=("w1",),
)
OMIETKY = _task(
    "t3",
    "Omietky",
    AGATOVA,
    "done",
    date_from=TODAY - timedelta(days=9),
    done_on=TODAY - timedelta(days=3),
    assignees=("w2",),
)
ZRUSENA = _task("t4", "Zrušená úloha", AGATOVA, "cancelled", assignees=("w2",))
STARA = _task(
    "t5", "Stará hotová", AGATOVA, "done", done_on=TODAY - timedelta(days=30), assignees=("w2",)
)
BEZ_TERMINU = _task("t6", "Upratanie", AGATOVA, "todo")

SNAP = Snapshot(
    workers=[MIKUS, HORAK, MALY],
    sites=[ROVINKA, AGATOVA],
    tasks=[BETON, MUROVANIE, OMIETKY, ZRUSENA, STARA, BEZ_TERMINU],
)

D1 = TODAY - timedelta(days=1)  # Wednesday
D2 = TODAY - timedelta(days=2)  # Tuesday


def _report(user: str, day: date, status: str = "submitted", **data: object) -> ReportFact:
    return ReportFact(
        user_id=user,
        day=iso(day),
        status=status,
        submitted_at=f"{iso(day)} 16:00:00" if status == "submitted" else None,
        data=ReportData(**data),  # type: ignore[arg-type]
    )


REPORTS = [
    _report(
        "w1",
        D1,
        arrival="06:30",
        departure="15:00",
        break_minutes=30,
        tasks=[
            TaskEntry(task_id="t1", status="in_progress", progress=60, hours=5.5, note="zalievka"),
            TaskEntry(task_id="t2", status="not_started"),
            TaskEntry(task_id="t3", status="done", hours=2),
            TaskEntry(task_id="ghost", status="done"),  # task no longer exists
            TaskEntry(task_id="t6", status=None),  # not answered yet
        ],
        extra_work=[
            ExtraWork(description="Upratovanie dvora", hours=1),
            ExtraWork(description="Výpomoc"),
        ],
        problems=["chýba armatúra", "pokazená miešačka"],
    ),
    _report("w2", D2, absent=True, absence_reason="lekár"),
    _report("w2", D1, status="draft", arrival="07:00"),
]


@pytest.fixture(autouse=True)
def fake_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        answer,
        "reports",
        lambda start, end: [r for r in REPORTS if iso(start) <= r.day <= iso(end)],
    )


def ctx(
    *intents: Intent,
    workers: list[WorkerRef] | None = None,
    sites: list[SiteRef] | None = None,
    tasks: list[TaskFact] | None = None,
    period: Period | None = None,
    viewer: Viewer = ADMIN,
    snap: Snapshot = SNAP,
) -> Ctx:
    query = FirmaQuery(
        intents=list(intents) or ["worker_day"],
        workers=workers or [],
        sites=sites or [],
        tasks=tasks or [],
        period=period,
    )
    return Ctx(query=query, snap=snap, viewer=viewer, today=TODAY)


def day_period(d: date) -> Period:
    return Period(d, d, True)


# --- small formatters ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "text"),
    [(7.5, "7,5 h"), (8, "8 h"), (0.25, "0,25 h"), (0, "0 h"), (12.304, "12,3 h"), (10.0, "10 h")],
)
def test_hours_format(value: float, text: str) -> None:
    assert hours(value) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [(None, "?"), ("", "?"), ("06:30", "6:30"), ("15:00", "15:00"), ("abc", "abc")],
)
def test_hhmm(value: str | None, text: str) -> None:
    assert hhmm(value) == text


def test_term_variants() -> None:
    d1, d2 = TODAY - timedelta(days=5), TODAY
    assert term(_task("x", "x", ROVINKA, date_from=d1, date_to=d2)) == (
        f"{fmt_day(d1, weekday=False)} – {fmt_day(d2, weekday=False)}"
    )
    assert term(_task("x", "x", ROVINKA, date_from=d1, date_to=d1)) == fmt_day(d1, weekday=False)
    assert term(_task("x", "x", ROVINKA, date_to=d2)) == f"do {fmt_day(d2, weekday=False)}"
    assert term(_task("x", "x", ROVINKA, date_from=d1)) == f"od {fmt_day(d1, weekday=False)}"
    assert term(BEZ_TERMINU) == "bez termínu"


def test_status_label_variants() -> None:
    assert status_label(BETON) == "rozpracovaná 60 %"
    assert status_label(OMIETKY) == f"hotová {fmt_day(TODAY - timedelta(days=3), weekday=False)}"
    assert status_label(_task("x", "x", ROVINKA, "done")) == "hotová"
    assert status_label(ZRUSENA) == "zrušená"
    assert status_label(BEZ_TERMINU) == "na rade"


def test_days_late() -> None:
    assert days_late(BETON, TODAY) == 3
    assert days_late(MUROVANIE, TODAY) == 0  # deadline still ahead
    assert days_late(OMIETKY, TODAY) == 0  # done tasks are never late
    assert days_late(BEZ_TERMINU, TODAY) == 0  # no deadline
    assert days_late(_task("x", "x", ROVINKA, date_to=TODAY), TODAY) == 0  # due today is not late


def test_blocker_only_for_open_dependency() -> None:
    assert blocker(MUROVANIE, SNAP) is BETON
    assert blocker(BETON, SNAP) is None  # no dependency
    waits_for_done = _task("m", "m", ROVINKA, depends_on="d")
    done_dep = Snapshot([], [], [_task("d", "dep", ROVINKA, "done"), waits_for_done])
    assert blocker(waits_for_done, done_dep) is None
    cancelled = Snapshot([], [], [_task("d", "dep", ROVINKA, "cancelled"), waits_for_done])
    assert blocker(waits_for_done, cancelled) is None
    dangling = _task("y", "y", ROVINKA, depends_on="missing")
    assert blocker(dangling, Snapshot([], [], [dangling])) is None
    assert blocker(OMIETKY, SNAP) is None  # finished task is not waiting for anything


def test_names_sorted_and_unknown_ids_dropped() -> None:
    assert names(["w2", "zzz", "w1"], SNAP) == ["Jozef Mikuš", "Peter Horák"]
    assert names([], SNAP) == []


def test_attendance_text_variants() -> None:
    assert (
        attendance_text(ReportData(absent=True, absence_reason="lekár")) == "neprítomnosť — lekár"
    )
    assert attendance_text(ReportData(absent=True)) == "neprítomnosť"
    full = ReportData(arrival="06:30", departure="15:00", break_minutes=30)
    assert attendance_text(full) == "6:30 – 15:00, prestávka 30 min, odpracované 8 h"
    assert attendance_text(ReportData(arrival="07:00")) == "7:00 – ?"
    assert attendance_text(ReportData()) == "bez časov"


def test_markdown_blocks_separates_paragraphs_but_keeps_lists_tight() -> None:
    text = "**Nadpis**\n- a\n- b\nPosledný riadok\n\n\n**Druhý**"
    assert markdown_blocks(text) == "**Nadpis**\n\n- a\n- b\n\nPosledný riadok\n\n**Druhý**"
    assert markdown_blocks("") == ""


# --- task_line / planned_on ------------------------------------------------------------


def test_task_line_full() -> None:
    line = task_line(MUROVANIE, ctx(), people=True)
    assert line.startswith("- **Murovanie** · Rovinka · na rade · ")
    assert "čaká na: Betonáž stropu" in line
    assert line.endswith("Jozef Mikuš")


def test_task_line_late_and_unassigned() -> None:
    line = task_line(BETON, ctx(), site=False, people=True)
    assert "Rovinka" not in line
    assert "po termíne 3 dni" in line
    assert "Jozef Mikuš, Peter Horák" in line
    assert task_line(BEZ_TERMINU, ctx(), people=True).endswith("zatiaľ nikto nepridelený")


def test_task_line_coworkers_exclude_the_worker_themself() -> None:
    line = task_line(BETON, ctx(), coworkers_of="w1")
    assert line.endswith("spolu s Peter Horák")
    assert "spolu s" not in task_line(MUROVANIE, ctx(), coworkers_of="w1")  # nobody else on it


def test_planned_on_today_uses_status_and_dependencies() -> None:
    planned = planned_on("w1", TODAY, ctx())
    assert [t.id for t in planned] == ["t1"]  # t2 waits for t1


def test_planned_on_past_day_uses_dates_not_current_status() -> None:
    # on the day before yesterday t2 was already within its term, regardless of the dependency
    assert {t.id for t in planned_on("w1", TODAY - timedelta(days=2), ctx())} == {"t1", "t2"}
    # w2 finished Omietky 3 days ago; it is still "planned" until then, gone afterwards
    assert "t3" in {t.id for t in planned_on("w2", TODAY - timedelta(days=3), ctx())}
    assert "t3" not in {t.id for t in planned_on("w2", TODAY - timedelta(days=2), ctx())}
    assert "t4" not in {
        t.id for t in planned_on("w2", TODAY - timedelta(days=3), ctx())
    }  # cancelled


# --- worker_day ------------------------------------------------------------------------


def test_worker_day_past_with_report() -> None:
    text = answer.worker_day(ctx("worker_day", workers=[MIKUS], period=day_period(D1)))
    assert text.startswith(f"**Jozef Mikuš — {fmt_day_long(D1, TODAY)}**")
    assert "Úlohy podľa plánu" in text
    assert "Denný výpis (odovzdaný): 6:30 – 15:00, prestávka 30 min, odpracované 8 h" in text
    assert "- Betonáž stropu: rozpracovaná 60 %, 5,5 h — zalievka" in text
    assert "- Murovanie: nerobilo sa" in text
    assert "- Omietky: hotová, 2 h" in text
    assert "ghost" not in text and "Upratanie" not in text  # unknown / unanswered entries skipped
    assert "- Mimo plánu: Upratovanie dvora (1 h)" in text
    assert "- Mimo plánu: Výpomoc" in text and "Výpomoc (" not in text
    assert "- Problémy: chýba armatúra; pokazená miešačka" in text


def test_worker_day_today_without_report_and_without_tasks() -> None:
    text = answer.worker_day(ctx("worker_day", workers=[HORAK], period=day_period(TODAY)))
    assert "Naplánované úlohy" in text
    assert "Denný výpis: zatiaľ nie je." in text


def test_worker_day_future_has_no_report_block() -> None:
    tomorrow = TODAY + timedelta(days=1)
    text = answer.worker_day(ctx("worker_day", workers=[MIKUS], period=day_period(tomorrow)))
    assert "Naplánované úlohy" in text
    assert "výpis" not in text.lower()


def test_worker_day_absent_report_stops_after_header() -> None:
    text = answer.worker_day(ctx("worker_day", workers=[HORAK], period=day_period(D2)))
    assert "Denný výpis (odovzdaný): neprítomnosť — lekár" in text


def test_worker_day_draft_report_status() -> None:
    text = answer.worker_day(ctx("worker_day", workers=[HORAK], period=day_period(D1)))
    assert "Denný výpis (rozpracovaný)" in text


def test_worker_day_no_tasks_that_day() -> None:
    text = answer.worker_day(ctx("worker_day", workers=[MALY], period=day_period(TODAY)))
    assert "Na tento deň nemá naplánovanú žiadnu úlohu." in text


def test_worker_day_without_named_worker_is_site_overview() -> None:
    assert answer.worker_day(ctx("worker_day")) == answer.site_overview(ctx("site_overview"))


# --- worker_tasks ----------------------------------------------------------------------


def test_worker_tasks_for_named_worker() -> None:
    text = answer.worker_tasks(ctx("worker_tasks", workers=[HORAK]))
    assert "**Peter Horák — pridelené úlohy**" in text
    assert "Otvorené (1):" in text and "Betonáž stropu" in text
    assert "Hotové za posledných 14 dní (1):" in text and "Omietky" in text
    assert "Stará hotová" not in text  # finished 30 days ago
    assert "Zrušená úloha" not in text  # cancelled tasks never show


def test_worker_tasks_named_worker_without_open_tasks() -> None:
    assert "Žiadnu otvorenú úlohu nemá." in answer.worker_tasks(ctx("worker_tasks", workers=[MALY]))


def test_worker_tasks_whole_team_grouped_by_site() -> None:
    text = answer.worker_tasks(ctx("worker_tasks"))
    assert text.startswith("**Otvorené úlohy** (3)")
    assert text.index("Agátová:") < text.index("Rovinka:")  # sites sorted alphabetically
    assert "zatiaľ nikto nepridelený" in text  # Upratanie


def test_worker_tasks_site_with_no_open_tasks() -> None:
    quiet = Snapshot([MIKUS], [ROVINKA, AGATOVA], [OMIETKY])
    text = answer.worker_tasks(ctx("worker_tasks", sites=[AGATOVA], snap=quiet))
    assert text == "Žiadne otvorené úlohy — Agátová."


def test_worker_tasks_worker_viewer_sees_only_own() -> None:
    text = answer.worker_tasks(ctx("worker_tasks", viewer=Viewer("worker", "w2")))
    assert "Betonáž stropu" in text
    assert "Murovanie" not in text  # w2 is not assigned to it


# --- attendance ------------------------------------------------------------------------


def test_attendance_future_day() -> None:
    tomorrow = TODAY + timedelta(days=1)
    assert "deň nenastal" in answer.attendance(ctx("attendance", period=day_period(tomorrow)))


def test_attendance_lists_everyone_active() -> None:
    text = answer.attendance(ctx("attendance", period=day_period(D1)))
    assert text.startswith(f"**Dochádzka — {fmt_day_long(D1, TODAY)}**")
    assert "- **Jozef Mikuš**: 6:30 – 15:00, prestávka 30 min, odpracované 8 h" in text
    assert "- **Peter Horák**: 7:00 – ? · výpis rozpracovaný" in text  # draft is flagged
    assert "Marek Malý" not in text  # inactive worker is not part of the team


def test_attendance_missing_report() -> None:
    text = answer.attendance(ctx("attendance", period=day_period(TODAY)))
    assert "- **Jozef Mikuš**: výpis chýba" in text


def test_attendance_with_no_active_workers() -> None:
    empty = Snapshot([MALY], [], [])
    text = answer.attendance(ctx("attendance", period=day_period(D1), snap=empty))
    assert text.endswith("V tíme nie sú aktívni pracovníci.")


# --- hours -----------------------------------------------------------------------------


def test_hours_period_entirely_in_future() -> None:
    future = Period(TODAY + timedelta(days=3), TODAY + timedelta(days=9), True)
    assert "ešte nie sú odpracované hodiny" in answer.hours_answer(ctx("hours", period=future))


def test_hours_single_worker_detail() -> None:
    period = Period(TODAY - timedelta(days=7), TODAY, True)
    text = answer.hours_answer(ctx("hours", workers=[MIKUS], period=period))
    assert "spolu **8 h** z 1 odovzdaných výpisov." in text
    assert f"- {fmt_day(D1)}: 8 h (6:30 – 15:00)" in text
    assert "Pracovné dni bez odovzdaného výpisu:" in text
    assert fmt_day(D1) not in text.split("Pracovné dni bez odovzdaného výpisu:")[1]


def test_hours_single_worker_absence_and_pending_drafts() -> None:
    period = Period(D2, D1, True)
    text = answer.hours_answer(ctx("hours", workers=[HORAK], period=period))
    assert "spolu **0 h** z 0 odovzdaných výpisov." in text
    assert f"- {fmt_day(D2)}: neprítomnosť — lekár" in text
    assert f"Neodovzdané (nezarátané): {fmt_day(D1)}" in text  # draft is not counted


def test_hours_whole_team_with_grand_total() -> None:
    period = Period(D2, D1, True)
    text = answer.hours_answer(ctx("hours", period=period))
    assert "- **Jozef Mikuš**: 8 h (1 výpisov, 1 prac. dní bez výpisu)" in text
    assert "- **Peter Horák**: 0 h (0 výpisov, 1 prac. dní bez výpisu)" in text
    assert text.endswith("Spolu: **8 h** (len odovzdané výpisy).")


def test_hours_period_is_clamped_to_today() -> None:
    period = Period(D1, TODAY + timedelta(days=30), True)
    text = answer.hours_answer(ctx("hours", workers=[MIKUS], period=period))
    assert "spolu **8 h**" in text


def test_hours_default_period_is_current_month() -> None:
    text = answer.hours_answer(ctx("hours", workers=[MIKUS]))
    assert "október 2026 (do dnes)" in text


# --- absences / missing reports --------------------------------------------------------


def test_absences_lists_reason_and_workdays_without_report() -> None:
    period = Period(D2, D1, True)
    text = answer.absences(ctx("absences", period=period))
    assert f"- **Peter Horák**: {fmt_day(D2)} — lekár" in text
    assert "Pracovné dni bez výpisu (dôvod nepoznám):" in text
    assert f"- Jozef Mikuš: {fmt_day(D2)}" in text  # Mikuš has no report for Tuesday


def test_absences_none_found() -> None:
    period = Period(D1, D1, True)
    text = answer.absences(ctx("absences", period=period))
    assert "Nikto nenahlásil neprítomnosť." in text


def test_absences_future_period() -> None:
    future = Period(TODAY + timedelta(days=2), TODAY + timedelta(days=4), True)
    assert "ešte nie sú údaje" in answer.absences(ctx("absences", period=future))


def test_missing_reports_variants() -> None:
    tomorrow = TODAY + timedelta(days=1)
    assert "ešte neodovzdávajú" in answer.missing_reports(
        ctx("missing_reports", period=day_period(tomorrow))
    )

    text = answer.missing_reports(ctx("missing_reports", period=day_period(D1)))
    assert "(1)" in text
    assert "- **Peter Horák**: rozpracovaný" in text  # draft, not submitted

    text = answer.missing_reports(ctx("missing_reports", period=day_period(TODAY)))
    assert "(2)" in text and "nezačatý" in text

    only_mikus = Snapshot([MIKUS], [], [])
    ok = answer.missing_reports(ctx("missing_reports", period=day_period(D1), snap=only_mikus))
    assert ok == f"Za {fmt_day_long(D1, TODAY)} odovzdali výpis všetci aktívni pracovníci."


# --- site_overview / task_people -------------------------------------------------------


def test_site_overview_for_named_site() -> None:
    text = answer.site_overview(ctx("site_overview", sites=[ROVINKA]))
    assert text.startswith(f"**Rovinka — {fmt_day_long(TODAY, TODAY)}**")
    assert "Pracujú: Jozef Mikuš (Betonáž stropu); Peter Horák (Betonáž stropu)" in text
    assert "Otvorené úlohy: 2 (rozpracované 1, na rade 1), po termíne 1" in text
    assert "čaká na: Betonáž stropu" in text


def test_site_overview_lists_recent_done_only() -> None:
    text = answer.site_overview(ctx("site_overview", sites=[AGATOVA]))
    assert "Hotové za posledných 14 dní: Omietky" in text
    assert "Stará hotová" not in text
    # the only active Agátová task (Upratanie) has nobody assigned
    assert "Na tento deň nemá nikto naplánovanú úlohu." in text


def test_site_overview_without_named_sites_lists_only_sites_with_open_tasks() -> None:
    both = answer.site_overview(ctx("site_overview"))
    assert "**Rovinka" in both and "**Agátová" in both
    only_done = Snapshot([MIKUS], [ROVINKA, AGATOVA], [OMIETKY])
    assert answer.site_overview(ctx("site_overview", snap=only_done)) == (
        "Žiadna stavba nemá otvorené úlohy."
    )


def test_task_people_for_named_task_and_fallback() -> None:
    text = answer.task_people(ctx("task_people", tasks=[BETON, BEZ_TERMINU]))
    assert "- **Betonáž stropu** (Rovinka): Jozef Mikuš, Peter Horák · rozpracovaná 60 %" in text
    assert "- **Upratanie** (Agátová): zatiaľ nikto nepridelený · na rade · bez termínu" in text
    assert answer.task_people(ctx("task_people")) == answer.site_overview(ctx("site_overview"))


# --- overdue / blocked / problems ------------------------------------------------------


def test_overdue_for_admin_lists_people() -> None:
    text = answer.overdue(ctx("overdue"))
    assert text.startswith("**Úlohy po termíne** (1)")
    assert "Betonáž stropu" in text and "po termíne 3 dni" in text
    assert "Jozef Mikuš, Peter Horák" in text


def test_overdue_scope_label_and_empty() -> None:
    text = answer.overdue(ctx("overdue", workers=[MIKUS], sites=[ROVINKA]))
    assert "(Jozef Mikuš, Rovinka)" in text
    empty = answer.overdue(ctx("overdue", sites=[AGATOVA]))
    assert empty == "Žiadna otvorená úloha (Agátová) nie je po termíne."


def test_overdue_for_worker_hides_other_names_scope_and_shows_coworkers() -> None:
    text = answer.overdue(ctx("overdue", viewer=Viewer("worker", "w1")))
    assert "spolu s Peter Horák" in text
    assert answer._scope_label(ctx("overdue", workers=[HORAK], viewer=Viewer("worker", "w1"))) == ""


def test_blocked_lists_waiting_tasks() -> None:
    text = answer.blocked(ctx("blocked"))
    assert text.startswith("**Úlohy čakajúce na inú úlohu** (1)")
    assert "**Murovanie** (Rovinka) čaká na **Betonáž stropu**" in text
    assert text.endswith("· Jozef Mikuš")


def test_blocked_filters_by_task_and_reports_none() -> None:
    assert "čaká na" in answer.blocked(ctx("blocked", tasks=[BETON]))  # as the blocking task
    assert "čaká na" in answer.blocked(ctx("blocked", tasks=[MUROVANIE]))  # as the waiting task
    assert answer.blocked(ctx("blocked", tasks=[OMIETKY])) == (
        "Žiadna otvorená úloha nečaká na inú úlohu."
    )


def test_problems_default_week_and_site_filter() -> None:
    text = answer.problems(ctx("problems"))
    assert "**Nahlásené problémy" in text
    assert f"- {fmt_day(D1)} · **Jozef Mikuš**: chýba armatúra" in text
    assert "pokazená miešačka" in text
    on_site = answer.problems(ctx("problems", sites=[ROVINKA]))
    assert "chýba armatúra" in on_site  # report touched tasks on Rovinka
    assert "chýba armatúra" in answer.problems(ctx("problems", sites=[AGATOVA]))  # entry for t3
    unrelated = SiteRef("s3", "Nová stavba", True)
    assert "chýba armatúra" not in answer.problems(ctx("problems", sites=[unrelated]))


def test_problems_none_reported_and_unknown_author() -> None:
    quiet = answer.problems(ctx("problems", period=day_period(D2)))
    assert quiet.endswith("Žiadne problémy neboli nahlásené.")
    # only subjects (named workers) are listed
    only_horak = answer.problems(ctx("problems", workers=[HORAK]))
    assert "Jozef Mikuš" not in only_horak


# --- render ----------------------------------------------------------------------------


def test_render_joins_several_intents() -> None:
    text = answer.render(ctx("attendance", "missing_reports", period=day_period(D1)))
    assert "**Dochádzka" in text and "**Bez odovzdaného výpisu" in text
    assert text.index("**Dochádzka") < text.index("**Bez odovzdaného výpisu")
    assert "\n\n**Bez odovzdaného výpisu" in text  # blocks are separated by a blank line


@pytest.mark.parametrize("intent", sorted(answer.RENDERERS))
def test_every_renderer_runs_on_the_sample_world(intent: Intent) -> None:
    renderer: Callable[[Ctx], str] = answer.RENDERERS[intent]
    out = renderer(ctx(intent, period=day_period(D1)))
    assert isinstance(out, str) and out.strip()
    assert intent in answer.STEP_LABELS


def test_reports_are_cached_per_range_and_filtered_for_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[date, date]] = []
    original = answer.reports

    def counting(start: date, end: date) -> list[ReportFact]:
        calls.append((start, end))
        return original(start, end)

    monkeypatch.setattr(answer, "reports", counting)
    c = ctx("hours", viewer=Viewer("worker", "w1"))
    first = c.reports(D2, D1)
    assert c.reports(D2, D1) is first
    assert len(calls) == 1
    assert {r.user_id for r in first} == {"w1"}  # a worker never sees colleagues' reports

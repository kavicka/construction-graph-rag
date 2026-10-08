"""Odpovede z firemných údajov — šablóny, ktoré plní kód. LLM do textu nezasahuje.

Každý čas, dátum, počet hodín aj percento pochádza priamo z DB; formát je slovenský
(„št 8. 10.“, „6:30“, „7,5 h“). Texty sú rodovo neutrálne.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.workforce.qa.access import Viewer
from app.workforce.qa.data import ReportFact, Snapshot, TaskFact, WorkerRef, reports
from app.workforce.qa.dates import Period, fmt_day, fmt_day_long, fmt_period, workdays
from app.workforce.qa.intents import FirmaQuery, Intent
from app.workforce.report_model import ReportData
from app.workforce.tasks import is_active_on

DONE_RECENT_DAYS = 14
OPEN = ("todo", "in_progress")


@dataclass
class Ctx:
    query: FirmaQuery
    snap: Snapshot
    viewer: Viewer
    today: date
    _reports: dict[tuple[date, date], list[ReportFact]] = field(default_factory=dict)

    def reports(self, start: date, end: date) -> list[ReportFact]:
        key = (start, end)
        if key not in self._reports:
            rows = reports(start, end)
            if self.viewer.own_only:
                rows = [r for r in rows if r.user_id == self.viewer.user_id]
            self._reports[key] = rows
        return self._reports[key]

    def visible_tasks(self) -> list[TaskFact]:
        tasks = [t for t in self.snap.tasks if t.status != "cancelled"]
        if self.viewer.own_only:
            tasks = [t for t in tasks if self.viewer.user_id in t.assignees]
        if self.query.sites:
            site_ids = {s.id for s in self.query.sites}
            tasks = [t for t in tasks if t.site_id in site_ids]
        if self.query.workers:
            ids = {w.id for w in self.query.workers}
            tasks = [t for t in tasks if ids & set(t.assignees)]
        return tasks

    def subjects(self) -> list[WorkerRef]:
        """Pracovníci, o ktorých je reč — menovaní, inak všetci aktívni (len admin)."""
        if self.query.workers:
            return self.query.workers
        return [w for w in self.snap.workers if w.active]


# --- formát -----------------------------------------------------------------------------


def hours(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{text} h"


def hhmm(value: str | None) -> str:
    if not value:
        return "?"
    hh, _, mm = value.partition(":")
    return f"{int(hh)}:{mm}" if hh.isdigit() else value


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def term(task: TaskFact) -> str:
    start, end = _d(task.date_from), _d(task.date_to)
    if start and end:
        return (fmt_day(start, weekday=False) if start == end else
                f"{fmt_day(start, weekday=False)} – {fmt_day(end, weekday=False)}")
    if end:
        return f"do {fmt_day(end, weekday=False)}"
    if start:
        return f"od {fmt_day(start, weekday=False)}"
    return "bez termínu"


def status_label(task: TaskFact) -> str:
    if task.status == "in_progress":
        return f"rozpracovaná {task.progress} %"
    if task.status == "done":
        done = _d(task.done_on)
        return f"hotová {fmt_day(done, weekday=False)}" if done else "hotová"
    if task.status == "cancelled":
        return "zrušená"
    return "na rade"


def days_late(task: TaskFact, today: date) -> int:
    end = _d(task.date_to)
    if task.status not in OPEN or end is None or end >= today:
        return 0
    return (today - end).days


def blocker(task: TaskFact, snap: Snapshot) -> TaskFact | None:
    if task.status not in OPEN or not task.depends_on:
        return None
    dep = snap.task_by_id.get(task.depends_on)
    if dep is None or dep.status in ("done", "cancelled"):
        return None
    return dep


def _plural_days(n: int) -> str:
    return "1 deň" if n == 1 else f"{n} dni" if n < 5 else f"{n} dní"


def names(ids: list[str] | tuple[str, ...], snap: Snapshot) -> list[str]:
    by_id = snap.worker_by_id
    return sorted((by_id[i].name for i in ids if i in by_id), key=str.casefold)


def task_line(task: TaskFact, ctx: Ctx, *, site: bool = True, people: bool = False,
              coworkers_of: str | None = None) -> str:
    parts = [f"**{task.title}**"]
    if site:
        parts.append(task.site_name)
    parts.append(status_label(task))
    parts.append(term(task))
    if late := days_late(task, ctx.today):
        parts.append(f"po termíne {_plural_days(late)}")
    if dep := blocker(task, ctx.snap):
        parts.append(f"čaká na: {dep.title}")
    if people:
        who = names(task.assignees, ctx.snap)
        parts.append(", ".join(who) if who else "zatiaľ nikto nepridelený")
    elif coworkers_of:
        others = names([a for a in task.assignees if a != coworkers_of], ctx.snap)
        if others:
            parts.append("spolu s " + ", ".join(others))
    return "- " + " · ".join(parts)


def _report_of(rows: list[ReportFact], user_id: str, day: date) -> ReportFact | None:
    iso = day.isoformat()
    return next((r for r in rows if r.user_id == user_id and r.day == iso), None)


REPORT_STATUS = {
    "draft": "rozpracovaný",
    "processing": "spracúva sa",
    "questioning": "čaká na odpovede na otázky",
    "ready": "pripravený na odovzdanie",
    "submitted": "odovzdaný",
}


def attendance_text(data: ReportData) -> str:
    if data.absent:
        return "neprítomnosť" + (f" — {data.absence_reason}" if data.absence_reason else "")
    bits = []
    if data.arrival or data.departure:
        bits.append(f"{hhmm(data.arrival)} – {hhmm(data.departure)}")
    if data.break_minutes is not None:
        bits.append(f"prestávka {data.break_minutes} min")
    if data.worked_hours is not None:
        bits.append(f"odpracované {hours(data.worked_hours)}")
    return ", ".join(bits) if bits else "bez časov"


# --- typy otázok ------------------------------------------------------------------------


def _day(ctx: Ctx) -> date:
    return ctx.query.period.start if ctx.query.period else ctx.today


def planned_on(worker_id: str, day: date, ctx: Ctx) -> list[TaskFact]:
    mine = [t for t in ctx.snap.tasks if worker_id in t.assignees and t.status != "cancelled"]
    if day >= ctx.today:
        by_id = {t.id: t.as_dict() for t in ctx.snap.tasks}
        return [t for t in mine if is_active_on(t.as_dict(), day, by_id)]
    # minulosť: dnešný stav úlohy nehovorí, čo sa robilo vtedy → podľa termínov
    out = []
    for t in mine:
        start, done = _d(t.date_from), _d(t.done_on)
        if (start is None or start <= day) and (done is None or done >= day):
            out.append(t)
    return out


def worker_day(ctx: Ctx) -> str:
    day = _day(ctx)
    subjects = ctx.subjects() if ctx.query.workers else []
    if not subjects:
        return site_overview(ctx)
    rows = ctx.reports(day, day)
    blocks = []
    for w in subjects:
        lines = [f"**{w.name} — {fmt_day_long(day, ctx.today)}**"]
        planned = planned_on(w.id, day, ctx)
        if planned:
            label = "Naplánované úlohy" if day >= ctx.today else "Úlohy podľa plánu"
            lines.append(f"{label} ({len(planned)}):")
            lines += [task_line(t, ctx, coworkers_of=w.id) for t in planned]
        else:
            lines.append("Na tento deň nemá naplánovanú žiadnu úlohu.")
        if day <= ctx.today:
            lines.append("")
            lines += _report_block(_report_of(rows, w.id, day), ctx)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _report_block(report: ReportFact | None, ctx: Ctx) -> list[str]:
    if report is None:
        return ["Denný výpis: zatiaľ nie je."]
    data = report.data
    lines = [f"Denný výpis ({REPORT_STATUS.get(report.status, report.status)}): "
             f"{attendance_text(data)}"]
    if data.absent:
        return lines
    by_id = ctx.snap.task_by_id
    labels = {"done": "hotová", "in_progress": "rozpracovaná", "not_started": "nerobilo sa"}
    for e in data.tasks:
        task = by_id.get(e.task_id)
        if task is None or e.status is None:
            continue
        bits = [labels[e.status]]
        if e.status == "in_progress" and e.progress is not None:
            bits[0] += f" {e.progress} %"
        if e.hours:
            bits.append(hours(e.hours))
        line = f"- {task.title}: {', '.join(bits)}"
        if e.note:
            line += f" — {e.note}"
        lines.append(line)
    for x in data.extra_work:
        lines.append(f"- Mimo plánu: {x.description}" + (f" ({hours(x.hours)})" if x.hours else ""))
    if data.problems:
        lines.append("- Problémy: " + "; ".join(data.problems))
    return lines


def worker_tasks(ctx: Ctx) -> str:
    tasks = ctx.visible_tasks()
    recent = ctx.today - timedelta(days=DONE_RECENT_DAYS)
    open_tasks = [t for t in tasks if t.status in OPEN]
    done = [t for t in tasks if t.status == "done" and (_d(t.done_on) or ctx.today) >= recent]
    if ctx.query.workers:
        blocks = []
        for w in ctx.query.workers:
            mine_open = [t for t in open_tasks if w.id in t.assignees]
            mine_done = [t for t in done if w.id in t.assignees]
            lines = [f"**{w.name} — pridelené úlohy**"]
            if mine_open:
                lines.append(f"Otvorené ({len(mine_open)}):")
                lines += [task_line(t, ctx, coworkers_of=w.id) for t in mine_open]
            else:
                lines.append("Žiadnu otvorenú úlohu nemá.")
            if mine_done:
                lines.append(f"Hotové za posledných {DONE_RECENT_DAYS} dní ({len(mine_done)}):")
                lines += [task_line(t, ctx, coworkers_of=w.id) for t in mine_done]
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)
    if not open_tasks:
        return "Žiadne otvorené úlohy" + _site_suffix(ctx) + "."
    lines = [f"**Otvorené úlohy{_site_suffix(ctx)}** ({len(open_tasks)})"]
    for site_name, group in _by_site(open_tasks):
        lines.append(f"\n{site_name}:")
        lines += [task_line(t, ctx, site=False, people=True) for t in group]
    return "\n".join(lines)


def _site_suffix(ctx: Ctx) -> str:
    if not ctx.query.sites:
        return ""
    return " — " + ", ".join(s.name for s in ctx.query.sites)


def _by_site(tasks: list[TaskFact]) -> list[tuple[str, list[TaskFact]]]:
    groups: dict[str, list[TaskFact]] = {}
    for t in tasks:
        groups.setdefault(t.site_name, []).append(t)
    return sorted(groups.items(), key=lambda kv: kv[0].casefold())


def attendance(ctx: Ctx) -> str:
    day = _day(ctx)
    if day > ctx.today:
        return f"Dochádzka za {fmt_day_long(day, ctx.today)} ešte nie je — deň nenastal."
    rows = ctx.reports(day, day)
    lines = [f"**Dochádzka — {fmt_day_long(day, ctx.today)}**"]
    for w in ctx.subjects():
        report = _report_of(rows, w.id, day)
        if report is None:
            lines.append(f"- **{w.name}**: výpis chýba")
            continue
        state = "" if report.submitted else f" · výpis {REPORT_STATUS.get(report.status, '')}"
        lines.append(f"- **{w.name}**: {attendance_text(report.data)}{state}")
    if len(lines) == 1:
        lines.append("V tíme nie sú aktívni pracovníci.")
    return "\n".join(lines)


def _period(ctx: Ctx, default: Callable[[date], Period]) -> Period:
    return ctx.query.period or default(ctx.today)


def _this_month(today: date) -> Period:
    return Period(today.replace(day=1), today, False)


def _last_week(today: date) -> Period:
    return Period(today - timedelta(days=6), today, False)


def hours_answer(ctx: Ctx) -> str:
    period = _period(ctx, _this_month)
    end = min(period.end, ctx.today)
    if end < period.start:
        return f"Za obdobie {fmt_period(period, ctx.today)} ešte nie sú odpracované hodiny."
    rows = ctx.reports(period.start, end)
    title = fmt_period(Period(period.start, end, period.explicit), ctx.today)
    subjects = ctx.subjects()
    if len(subjects) == 1:
        return _hours_detail(subjects[0], rows, period.start, end, title)
    lines = [f"**Odpracované hodiny — {title}**"]
    grand = 0.0
    for w in subjects:
        total, count, missing, _ = _hours_of(w.id, rows, period.start, end)
        grand += total
        extra = f", {len(missing)} prac. dní bez výpisu" if missing else ""
        lines.append(f"- **{w.name}**: {hours(total)} ({count} výpisov{extra})")
    lines.append(f"\nSpolu: **{hours(grand)}** (len odovzdané výpisy).")
    return "\n".join(lines)


def _hours_of(user_id: str, rows: list[ReportFact], start: date,
              end: date) -> tuple[float, int, list[date], list[ReportFact]]:
    mine = [r for r in rows if r.user_id == user_id and r.submitted]
    counted = [r for r in mine if not r.data.absent and r.data.worked_hours is not None]
    total = round(sum(r.data.worked_hours or 0 for r in counted), 2)
    have = {r.day for r in mine}
    missing = [d for d in workdays(start, end) if d.isoformat() not in have]
    return total, len(counted), missing, mine


def _hours_detail(w: WorkerRef, rows: list[ReportFact], start: date, end: date,
                  title: str) -> str:
    total, count, missing, mine = _hours_of(w.id, rows, start, end)
    lines = [f"**{w.name} — {title}**: spolu **{hours(total)}** "
             f"z {count} odovzdaných výpisov."]
    for r in sorted(mine, key=lambda r: r.day):
        day = date.fromisoformat(r.day)
        if r.data.absent:
            lines.append(f"- {fmt_day(day)}: {attendance_text(r.data)}")
        elif r.data.worked_hours is not None:
            lines.append(f"- {fmt_day(day)}: {hours(r.data.worked_hours)} "
                         f"({hhmm(r.data.arrival)} – {hhmm(r.data.departure)})")
    pending = [r for r in rows if r.user_id == w.id and not r.submitted]
    if pending:
        lines.append("Neodovzdané (nezarátané): " +
                     ", ".join(fmt_day(date.fromisoformat(r.day)) for r in pending))
    if missing:
        lines.append("Pracovné dni bez odovzdaného výpisu: " +
                     ", ".join(fmt_day(d) for d in missing))
    return "\n".join(lines)


def absences(ctx: Ctx) -> str:
    period = _period(ctx, _this_month)
    end = min(period.end, ctx.today)
    if end < period.start:
        return f"Za obdobie {fmt_period(period, ctx.today)} ešte nie sú údaje."
    rows = ctx.reports(period.start, end)
    title = fmt_period(Period(period.start, end, period.explicit), ctx.today)
    lines = [f"**Neprítomnosť — {title}**"]
    found = False
    for w in ctx.subjects():
        absent = sorted((r for r in rows if r.user_id == w.id and r.data.absent),
                        key=lambda r: r.day)
        for r in absent:
            found = True
            reason = f" — {r.data.absence_reason}" if r.data.absence_reason else ""
            lines.append(f"- **{w.name}**: {fmt_day(date.fromisoformat(r.day))}{reason}")
    if not found:
        lines.append("Nikto nenahlásil neprítomnosť.")
    no_report = []
    for w in ctx.subjects():
        have = {r.day for r in rows if r.user_id == w.id}
        missing = [d for d in workdays(period.start, end) if d.isoformat() not in have]
        if missing:
            no_report.append(f"- {w.name}: " + ", ".join(fmt_day(d) for d in missing))
    if no_report:
        lines.append("\nPracovné dni bez výpisu (dôvod nepoznám):")
        lines += no_report
    return "\n".join(lines)


def missing_reports(ctx: Ctx) -> str:
    day = _day(ctx)
    if day > ctx.today:
        return f"Za {fmt_day_long(day, ctx.today)} sa výpisy ešte neodovzdávajú."
    rows = ctx.reports(day, day)
    missing = []
    for w in ctx.subjects():
        report = _report_of(rows, w.id, day)
        if report is None:
            missing.append(f"- **{w.name}**: nezačatý")
        elif not report.submitted:
            missing.append(f"- **{w.name}**: {REPORT_STATUS.get(report.status, report.status)}")
    when = fmt_day_long(day, ctx.today)
    if not missing:
        return f"Za {when} odovzdali výpis všetci aktívni pracovníci."
    return "\n".join([f"**Bez odovzdaného výpisu — {when}** ({len(missing)})", *missing])


def site_overview(ctx: Ctx) -> str:
    day = _day(ctx)
    tasks = ctx.visible_tasks()
    if ctx.query.sites:
        sites = ctx.query.sites
    else:
        with_open = {t.site_id for t in tasks if t.status in OPEN}
        sites = [s for s in ctx.snap.sites if s.id in with_open]
    if not sites:
        return "Žiadna stavba nemá otvorené úlohy."
    recent = ctx.today - timedelta(days=DONE_RECENT_DAYS)
    by_id = {t.id: t.as_dict() for t in ctx.snap.tasks}
    blocks = []
    for s in sites:
        site_tasks = [t for t in tasks if t.site_id == s.id]
        open_tasks = [t for t in site_tasks if t.status in OPEN]
        lines = [f"**{s.name} — {fmt_day_long(day, ctx.today)}**"]
        working = []
        for t in site_tasks:
            if is_active_on(t.as_dict(), day, by_id):
                for name in names(t.assignees, ctx.snap):
                    working.append(f"{name} ({t.title})")
        if working:
            lines.append("Pracujú: " + "; ".join(working))
        else:
            lines.append("Na tento deň nemá nikto naplánovanú úlohu.")
        late = sum(days_late(t, ctx.today) > 0 for t in open_tasks)
        in_progress = sum(t.status == "in_progress" for t in open_tasks)
        summary = (f"Otvorené úlohy: {len(open_tasks)} (rozpracované {in_progress}, "
                   f"na rade {len(open_tasks) - in_progress})")
        if late:
            summary += f", po termíne {late}"
        lines.append(summary)
        lines += [task_line(t, ctx, site=False, people=True) for t in open_tasks]
        done = [t for t in site_tasks
                if t.status == "done" and (_d(t.done_on) or ctx.today) >= recent]
        if done:
            lines.append(f"Hotové za posledných {DONE_RECENT_DAYS} dní: " +
                         ", ".join(t.title for t in done))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def task_people(ctx: Ctx) -> str:
    if not ctx.query.tasks:
        return site_overview(ctx)
    lines = []
    for t in ctx.query.tasks:
        who = names(t.assignees, ctx.snap)
        lines.append(f"- **{t.title}** ({t.site_name}): "
                     f"{', '.join(who) if who else 'zatiaľ nikto nepridelený'} · "
                     f"{status_label(t)} · {term(t)}")
    return "\n".join(lines)


def overdue(ctx: Ctx) -> str:
    late = sorted(((days_late(t, ctx.today), t) for t in ctx.visible_tasks()),
                  key=lambda x: -x[0])
    late = [(n, t) for n, t in late if n > 0]
    scope = _scope_label(ctx)
    if not late:
        return f"Žiadna otvorená úloha{scope} nie je po termíne."
    lines = [f"**Úlohy po termíne{scope}** ({len(late)})"]
    for _, t in late:
        lines.append(task_line(t, ctx, people=not ctx.viewer.own_only,
                               coworkers_of=ctx.viewer.user_id if ctx.viewer.own_only
                               else None))
    return "\n".join(lines)


def _scope_label(ctx: Ctx) -> str:
    bits = [w.name for w in ctx.query.workers] if not ctx.viewer.own_only else []
    bits += [s.name for s in ctx.query.sites]
    return f" ({', '.join(bits)})" if bits else ""


def blocked(ctx: Ctx) -> str:
    tasks = ctx.visible_tasks()
    pairs = [(t, dep) for t in tasks if (dep := blocker(t, ctx.snap))]
    if ctx.query.tasks:
        wanted = {t.id for t in ctx.query.tasks}
        pairs = [(t, dep) for t, dep in pairs if dep.id in wanted or t.id in wanted]
    if not pairs:
        return "Žiadna otvorená úloha nečaká na inú úlohu."
    lines = [f"**Úlohy čakajúce na inú úlohu** ({len(pairs)})"]
    for t, dep in pairs:
        who = names(t.assignees, ctx.snap)
        lines.append(f"- **{t.title}** ({t.site_name}) čaká na **{dep.title}** "
                     f"({status_label(dep)}, {term(dep)})" +
                     (f" · {', '.join(who)}" if who else ""))
    return "\n".join(lines)


def problems(ctx: Ctx) -> str:
    period = _period(ctx, _last_week)
    end = min(period.end, ctx.today)
    rows = ctx.reports(period.start, end)
    ids = {w.id for w in ctx.subjects()}
    site_task_ids = ({t.id for t in ctx.snap.tasks
                      if t.site_id in {s.id for s in ctx.query.sites}}
                     if ctx.query.sites else None)
    title = fmt_period(Period(period.start, end, period.explicit), ctx.today)
    lines = [f"**Nahlásené problémy — {title}**"]
    by_id = ctx.snap.worker_by_id
    for r in sorted(rows, key=lambda r: (r.day, r.user_id)):
        if r.user_id not in ids or not r.data.problems:
            continue
        if site_task_ids is not None and not {e.task_id for e in r.data.tasks} & site_task_ids:
            continue
        who = by_id[r.user_id].name if r.user_id in by_id else "?"
        for p in r.data.problems:
            lines.append(f"- {fmt_day(date.fromisoformat(r.day))} · **{who}**: {p}")
    if len(lines) == 1:
        lines.append("Žiadne problémy neboli nahlásené.")
    return "\n".join(lines)


RENDERERS: dict[Intent, Callable[[Ctx], str]] = {
    "worker_day": worker_day,
    "worker_tasks": worker_tasks,
    "attendance": attendance,
    "hours": hours_answer,
    "absences": absences,
    "missing_reports": missing_reports,
    "site_overview": site_overview,
    "task_people": task_people,
    "overdue": overdue,
    "blocked": blocked,
    "problems": problems,
}

STEP_LABELS: dict[Intent, str] = {
    "worker_day": "čo robí v daný deň",
    "worker_tasks": "pridelené úlohy",
    "attendance": "dochádzka",
    "hours": "odpracované hodiny",
    "absences": "neprítomnosť",
    "missing_reports": "chýbajúce výpisy",
    "site_overview": "prehľad stavby",
    "task_people": "kto robí na úlohe",
    "overdue": "úlohy po termíne",
    "blocked": "čakajúce úlohy",
    "problems": "nahlásené problémy",
}


def markdown_blocks(text: str) -> str:
    """Riadky šablón → markdown: prázdny riadok medzi všetkým okrem po sebe idúcich položiek
    zoznamu. Inak by markdown zlial nadpis s ďalším riadkom do jedného odseku a riadok za
    zoznamom prilepil k poslednej položke."""
    out: list[str] = []
    prev = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        if out and not (line.startswith("- ") and prev.startswith("- ")):
            out.append("")
        out.append(line)
        prev = line
    return "\n".join(out)


def render(ctx: Ctx) -> str:
    return markdown_blocks("\n\n".join(RENDERERS[i](ctx) for i in ctx.query.intents))

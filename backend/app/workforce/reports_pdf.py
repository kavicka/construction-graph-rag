"""PDF výpisy: za pracovníka (po odovzdaní) a súhrnný denný za firmu.

Čísla v PDF (hodiny, súčty, počty) dosádza šablóna z overených dát výpisov — LLM tu
nepíše nič. Vzhľad (farby, písmo) je z témy aplikácie, hlavička/pätička z nastavení firmy.
"""

from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.company import CompanySettings, load_company
from app.docgen.pdf import render_pdf
from app.docgen.theme import THEME
from app.workforce import store
from app.workforce.config import get_workforce_settings
from app.workforce.report_model import STATUS_LABELS, ReportData

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

WEEKDAYS = ["pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa"]


def fmt_hours(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def day_label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{WEEKDAYS[d.weekday()]} {d.day}. {d.month}. {d.year}"


def _local_time(iso: str | None) -> str | None:
    if not iso:
        return None
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone().strftime("%d. %m. %Y %H:%M")


def _branding(company: CompanySettings) -> dict[str, Any]:
    return {
        "company": company,
        "theme": THEME,
        "hours": fmt_hours,
    }


def _render(template: str, out: Path, **ctx: Any) -> int:
    html = _env.get_template(template).render(**_branding(load_company()), **ctx)
    pdf = render_pdf(html, allowed_dirs=[])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".part")
    tmp.write_bytes(pdf.data)
    tmp.replace(out)
    return pdf.pages


def render_worker_report(report_id: str) -> Path:
    report = store.get_report(report_id)
    if report is None:
        raise KeyError(report_id)
    worker = store.get_worker(report["user_id"])
    name = worker["name"] if worker else "Pracovník"
    data = ReportData.model_validate_json(report["data_json"] or "{}")
    rows = []
    for entry in data.tasks:
        task = store.get_task(entry.task_id)
        rows.append(
            {
                "title": task["title"] if task else "(zmazaná úloha)",
                "site": task["site_name"] if task else "",
                "status": entry.status or "not_started",
                "status_label": STATUS_LABELS.get(entry.status or "", "—"),
                "progress": entry.progress,
                "hours": entry.hours,
                "note": entry.note,
            }
        )
    qa = [
        {"question": q["text"], "answer": q["answer"]}
        for q in store.list_questions(report_id, status="answered")
        if q["answer"]
    ]
    out = get_workforce_settings().reports_dir / f"report-{report_id}.pdf"
    pages = _render(
        "worker_report.html.j2",
        out,
        title=f"Denný výpis — {name} — {report['day']}",
        worker_name=name,
        day_label=day_label(report["day"]),
        submitted_at=_local_time(report["submitted_at"]),
        edited_by_owner=bool(report["edited_by_owner"]),
        data=data,
        worked=data.worked_hours,
        task_rows=rows,
        qa=qa,
        transcript=report["transcript"],
    )
    store.update_report(report_id, pdf_path=str(out), pdf_pages=pages)
    return out


def summary_context(day: str) -> dict[str, Any]:
    """Dáta súhrnu za deň — oddelené od renderu, aby sa dali testovať bez PDF."""
    reports = {r["user_id"]: r for r in store.list_reports(day=day)}
    workers = store.list_workers(active_only=True)
    known = {w["id"] for w in workers}
    # aj deaktivovaný pracovník, ktorý v ten deň výpis odovzdal
    for uid in reports:
        if uid not in known and (inactive := store.get_worker(uid)):
            workers.append(inactive)

    worker_rows, missing, problems, extra = [], [], [], []
    task_agg: dict[str, dict[str, Any]] = {}
    total_hours = 0.0
    submitted = 0
    for w in workers:
        r = reports.get(w["id"])
        row: dict[str, Any] = {
            "name": w["name"],
            "arrival": None,
            "departure": None,
            "break_minutes": None,
            "worked": None,
            "state": "missing",
            "absence_reason": "",
        }
        if r is None or r["status"] != "submitted":
            row["state"] = "open" if r is not None and r["status"] != "draft" else "missing"
            missing.append(w["name"])
            worker_rows.append(row)
            continue
        submitted += 1
        data = ReportData.model_validate_json(r["data_json"] or "{}")
        if data.absent:
            row.update(state="absent", absence_reason=data.absence_reason)
            worker_rows.append(row)
            continue
        row.update(
            state="submitted",
            arrival=data.arrival,
            departure=data.departure,
            break_minutes=data.break_minutes,
            worked=data.worked_hours,
        )
        total_hours += data.worked_hours or 0
        worker_rows.append(row)
        for p in data.problems:
            problems.append({"worker": w["name"], "text": p})
        for x in data.extra_work:
            extra.append({"worker": w["name"], "description": x.description, "hours": x.hours})
        for entry in data.tasks:
            if entry.status == "not_started" and not entry.note:
                continue
            agg = task_agg.setdefault(
                entry.task_id,
                {
                    "workers": [],
                    "hours": 0.0,
                    "notes": [],
                    "statuses": [],
                    "progress": None,
                },
            )
            agg["workers"].append(w["name"])
            agg["hours"] += entry.hours or 0
            agg["statuses"].append(entry.status)
            if entry.progress is not None:
                agg["progress"] = max(agg["progress"] or 0, entry.progress)
            if entry.note:
                agg["notes"].append(f"{w['name']}: {entry.note}")

    sites: dict[str, dict[str, Any]] = defaultdict(lambda: {"name": "", "tasks": []})
    done_count = 0
    for task_id, agg in task_agg.items():
        task = store.get_task(task_id)
        if task is None:
            continue
        statuses = agg["statuses"]
        status = (
            "done"
            if "done" in statuses
            else "in_progress"
            if "in_progress" in statuses
            else "not_started"
        )
        done_count += status == "done"
        site = sites[task["site_id"]]
        site["name"] = task["site_name"]
        site["tasks"].append(
            {
                "title": task["title"],
                "workers": agg["workers"],
                "status": status,
                "status_label": STATUS_LABELS[status],
                "progress": 100 if status == "done" else agg["progress"],
                "hours": round(agg["hours"], 2),
                "notes": agg["notes"],
            }
        )
    return {
        "day": day,
        "workers": worker_rows,
        "worker_count": len(worker_rows),
        "submitted_count": submitted,
        "missing": missing,
        "total_hours": round(total_hours, 2),
        "done_count": done_count,
        "sites": sorted(sites.values(), key=lambda s: s["name"].casefold()),
        "problems": problems,
        "extra": extra,
    }


def render_daily_summary(day: str) -> Path:
    ctx = summary_context(day)
    out = get_workforce_settings().reports_dir / f"daily-{day}.pdf"
    pages = _render(
        "daily_summary.html.j2",
        out,
        title=f"Súhrnný denný výpis — {day}",
        day_label=day_label(day),
        generated_at=datetime.now().strftime("%d. %m. %Y %H:%M"),
        **ctx,
    )
    store.set_daily_summary(day, str(out), pages)
    return out


def summary_is_stale(day: str) -> bool:
    """Súhrn treba pregenerovať, ak po ňom niekto odovzdal alebo upravil výpis."""
    summary = store.get_daily_summary(day)
    if summary is None or not Path(summary["pdf_path"]).exists():
        return True
    created = summary["created_at"]
    return any(r["updated_at"] > created for r in store.list_reports(day=day))

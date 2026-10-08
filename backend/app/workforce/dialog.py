"""Stavový automat denného výpisu: vstup (hlas/text) → extrakcia → otázky → odovzdanie.

draft → processing → questioning ⇄ processing → ready → submitted

Odovzdať sa dá len keď find_gaps nič nenájde — kontrola beží na serveri pri každom
odovzdaní, UI ju len zrkadlí. Nejasná odpoveď: 1. pokus → konkrétnejšia otázka,
2. pokus → režim výberu (tlačidlá), ktorý sa vyplní vždy.
"""

import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.workforce import extract, speech, store
from app.workforce.config import get_workforce_settings
from app.workforce.questions import question_text
from app.workforce.report_model import ExtraWork, Gap, ReportData, find_gaps
from app.workforce.sk_numbers import number_candidates, parse_hhmm
from app.workforce.tasks import active_tasks_for, apply_report_statuses

logger = logging.getLogger(__name__)

CHOICES_AFTER = 2  # po koľkých nejasných odpovediach prejsť na tlačidlá


class ReportLocked(RuntimeError):
    """Výpis sa práve spracúva alebo je odovzdaný."""


class IncompleteReport(RuntimeError):
    def __init__(self, gaps: list[Gap]) -> None:
        super().__init__(f"{len(gaps)} nezodpovedaných otázok")
        self.gaps = gaps


def load_data(report: dict[str, Any]) -> ReportData:
    return ReportData.model_validate_json(report["data_json"] or "{}")


def save_data(report_id: str, data: ReportData) -> None:
    store.update_report(report_id, data_json=data.model_dump_json())


def report_tasks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Úlohy, na ktoré sa výpis pýta: aktívne v ten deň + tie, ktoré už vo výpise sú."""
    day = date.fromisoformat(report["day"])
    tasks = active_tasks_for(report["user_id"], day)
    have = {t["id"] for t in tasks}
    for entry in load_data(report).tasks:
        if entry.task_id not in have and (t := store.get_task(entry.task_id)):
            tasks.append(t)
            have.add(t["id"])
    return tasks


def _gap_of(question: dict[str, Any]) -> Gap:
    extra_index = None
    if question["kind"] == "extra":
        extra_index = int(question["key"].split(":", 1)[1])
    return Gap(question["key"], question["kind"], question["task_id"], question["detail"],
               extra_index)


def sync_questions(report_id: str, *, finish: bool = True) -> list[Gap]:
    """Otázky = presne chýbajúce údaje. Vyriešené sa zatvoria, nové pribudnú.

    finish=False nechá výpis v stave processing — volajúci ešte zapisuje (počet pokusov)
    a stav prepne sám; klient inak uvidí hotový výpis so starou otázkou."""
    report = store.get_report(report_id)
    assert report is not None
    data = load_data(report)
    tasks = report_tasks(report)
    by_id = {t["id"]: t for t in tasks}
    # pýtame sa len na otvorené úlohy — hotovú (napr. uzavretú majiteľom alebo kolegom)
    # pracovník môže spomenúť, ale nemusí
    active_ids = [t["id"] for t in active_tasks_for(report["user_id"],
                                                    date.fromisoformat(report["day"]))
                  if t["status"] in ("todo", "in_progress")]
    gaps = find_gaps(data, active_ids, tolerance=get_workforce_settings().hours_tolerance)
    gap_keys = {g.key for g in gaps}
    has_input = bool(report["transcript"].strip()) or bool(store.list_inputs(report_id))
    if not has_input and report["status"] != "submitted":
        # pred prvým výpisom sa nepýtame — pracovník najprv povie, čo robil
        store.update_report(report_id, status="draft", step=None)
        return gaps

    open_qs = {q["key"]: q for q in store.list_questions(report_id, status="open")}
    for key, resolved in open_qs.items():
        if key not in gap_keys:
            store.update_question(resolved["id"],
                                  status="answered" if resolved["answer"] else "obsolete")
    for pos, gap in enumerate(gaps):
        q = open_qs.get(gap.key)
        if q is None:
            store.insert_question(
                report_id, key=gap.key, kind=gap.kind, task_id=gap.task_id, detail=gap.detail,
                text=question_text(gap, 0, data, by_id), position=pos,
            )
        elif q["detail"] != gap.detail or gap.kind == "hours":
            # čiastočná odpoveď: otázka sa zúži na to, čo ešte chýba
            store.update_question(
                q["id"], detail=gap.detail, position=pos,
                text=question_text(gap, q["attempts"], data, by_id),
            )
        else:
            store.update_question(q["id"], position=pos)
    if finish:
        _finish(report_id, gaps)
    return gaps


def _finish(report_id: str, gaps: list[Gap]) -> None:
    report = store.get_report(report_id)
    if report is not None and report["status"] != "submitted":
        store.update_report(report_id, status="questioning" if gaps else "ready", step=None)


def _lock_for_processing(report_id: str, step: str) -> dict[str, Any]:
    report = store.get_report(report_id)
    if report is None:
        raise KeyError(report_id)
    if report["status"] in ("processing", "submitted"):
        raise ReportLocked(report["status"])
    store.update_report(report_id, status="processing", step=step, error=None)
    return report


def _finish_with_error(report_id: str, exc: Exception) -> None:
    logger.exception("spracovanie výpisu %s zlyhalo", report_id)
    sync_questions(report_id)
    store.update_report(report_id, error=str(exc)[:300])


def _stt_prompt(report: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    names = [w["name"] for w in store.list_workers(active_only=True)
             if w["id"] != report["user_id"]]
    return speech.vocabulary_prompt(
        [t["site_name"] for t in tasks], [t["title"] for t in tasks], names[:12]
    )


def begin_main_input(report_id: str, *, audio: bool = True) -> None:
    """Synchrónne zamkne výpis (status processing) — spracovanie potom beží na pozadí."""
    _lock_for_processing(report_id, "prepis" if audio else "analýza")


def process_main_input(
    report_id: str, *, text: str | None = None, audio_path: Path | None = None
) -> None:
    """Hlavný výpis (hlas alebo text) → prepis → extrakcia → otázky. Volá sa po begin_*."""
    try:
        report = store.get_report(report_id)
        assert report is not None
        tasks = report_tasks(report)
        if audio_path is not None:
            input_id = store.insert_input(report_id, "audio", "", audio_path=str(audio_path))
            text = speech.transcribe(audio_path, _stt_prompt(report, tasks))
            store.set_input_text(input_id, text)
        else:
            store.insert_input(report_id, "text", text or "")
        text = (text or "").strip()
        if not text:
            raise ValueError("v nahrávke nezaznela reč — skús to znova bližšie k mikrofónu")
        transcript = (report["transcript"] + "\n\n" + text).strip()
        store.update_report(report_id, transcript=transcript, step="analýza")
        data = extract.extract_report(text, tasks, load_data(report), report["day"])
        save_data(report_id, data)
        sync_questions(report_id)
    except Exception as exc:  # noqa: BLE001 — chyba sa zobrazí pracovníkovi, výpis žije ďalej
        _finish_with_error(report_id, exc)


# --- odpovede ---------------------------------------------------------------------------


class ChoiceAnswer(BaseModel):
    """Štruktúrovaná odpoveď z tlačidiel — bez LLM, len validácia."""

    time: str | None = None
    minutes: int | None = Field(default=None, ge=0, le=300)
    status: Literal["done", "in_progress", "not_started"] | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    hours: float | None = Field(default=None, ge=0, le=16)
    note: str | None = Field(default=None, max_length=500)
    problems: list[str] | None = None
    task_hours: dict[str, float] | None = None
    extra_hours: list[float] | None = None
    absence_reason: str | None = Field(default=None, max_length=300)


def _apply_choice(
    q: dict[str, Any], choice: ChoiceAnswer, data: ReportData, tasks: list[dict[str, Any]]
) -> ReportData:
    out = data.model_copy(deep=True)
    kind = q["kind"]
    if choice.absence_reason:
        # „Dnes som nepracoval“ sa dá zvoliť pri hocijakej otázke — inak by pracovník
        # uviazol v otázkach na časy, keď LLM neprítomnosť z výpisu nevyčítal
        out.absent, out.absence_reason = True, choice.absence_reason.strip()
        return out
    if kind in ("arrival", "departure") and parse_hhmm(choice.time):
        hm = parse_hhmm(choice.time)
        assert hm is not None
        setattr(out, kind, f"{hm[0]:02d}:{hm[1]:02d}")
    elif kind == "break" and choice.minutes is not None:
        out.break_minutes = choice.minutes
    elif kind == "task" and q["task_id"]:
        entry = out.upsert_entry(q["task_id"])
        if choice.status:
            entry.status = choice.status
        if choice.hours is not None:
            entry.hours = choice.hours
        if entry.status == "done":
            entry.progress = 100
        elif choice.progress is not None:
            entry.progress = choice.progress
        if choice.note:
            entry.note = choice.note.strip()
    elif kind == "extra":
        idx = int(q["key"].split(":", 1)[1])
        if idx < len(out.extra_work) and choice.hours is not None:
            out.extra_work[idx].hours = choice.hours
    elif kind == "problems" and choice.problems is not None:
        out.problems = [p.strip()[:300] for p in choice.problems if p.strip()]
        out.problems_answered = True
    elif kind == "hours":
        for task_id, hours in (choice.task_hours or {}).items():
            if any(t["id"] == task_id for t in tasks) and 0 <= hours <= 16:
                entry = out.upsert_entry(task_id)
                entry.hours = hours
        for idx, hours in enumerate(choice.extra_hours or []):
            if idx < len(out.extra_work) and 0 <= hours <= 16:
                out.extra_work[idx].hours = hours
        if choice.note and choice.note.strip():
            out.extra_work.append(ExtraWork(description=choice.note.strip()[:300],
                                            hours=choice.hours))
    return out


def _apply_text(
    q: dict[str, Any], answer: str, data: ReportData, tasks: list[dict[str, Any]]
) -> ReportData:
    out = data.model_copy(deep=True)
    kind = q["kind"]
    if kind in ("arrival", "departure"):
        value = extract.parse_time_answer(answer, afternoon=kind == "departure")
        if value:
            setattr(out, kind, value)
    elif kind == "break":
        minutes = extract.parse_break_answer(answer)
        if minutes is not None:
            out.break_minutes = minutes
    elif kind == "task" and q["task_id"]:
        task = next((t for t in tasks if t["id"] == q["task_id"]), None)
        if task is not None:
            changes = extract.parse_task_answer(task, q["text"], answer, q["detail"])
            existing = out.entry(task["id"])
            # dobrá poznámka z hlavného výpisu sa neprepisuje, okrem pýtaného dôvodu
            if existing and existing.note and "reason" not in q["detail"].split(","):
                changes.pop("note", None)
            if changes:
                entry = out.upsert_entry(task["id"])
                for key, value in changes.items():
                    setattr(entry, key, value)
                if entry.status == "done":
                    entry.progress = 100
    elif kind == "extra":
        idx = int(q["key"].split(":", 1)[1])
        cands = sorted(c for c in number_candidates(answer) if 0 < c <= 16)
        if idx < len(out.extra_work) and len(cands) == 1:
            out.extra_work[idx].hours = cands[0]
    elif kind == "problems":
        problems = extract.parse_problems_answer(answer)
        if problems is not None:
            out.problems = problems
            out.problems_answered = True
    elif kind == "hours":
        fixed = extract.parse_hours_answer(answer, out, tasks)
        if fixed is not None:
            out = fixed
    elif kind == "absence" and len(answer.strip()) >= 3:
        out.absent, out.absence_reason = True, answer.strip()[:300]
    return out


def begin_answer(report_id: str, question_id: str, *, audio: bool = True) -> dict[str, Any]:
    q = store.get_question(question_id)
    if q is None or q["report_id"] != report_id:
        raise KeyError(question_id)
    if q["status"] != "open":
        raise ReportLocked("otázka je už zodpovedaná")
    _lock_for_processing(report_id, "prepis" if audio else "analýza")
    return q


def process_answer(
    report_id: str, question_id: str, *, text: str | None = None,
    audio_path: Path | None = None, choice: ChoiceAnswer | None = None,
) -> None:
    try:
        report = store.get_report(report_id)
        q = store.get_question(question_id)
        assert report is not None and q is not None
        tasks = report_tasks(report)
        data = load_data(report)

        if choice is not None:
            store.insert_input(report_id, "choice", choice.model_dump_json(exclude_none=True),
                               question_id=question_id)
            new_data = _apply_choice(q, choice, data, tasks)
            answer = "(výber)"
        else:
            if audio_path is not None:
                input_id = store.insert_input(report_id, "audio", "", question_id=question_id,
                                              audio_path=str(audio_path))
                text = speech.transcribe(audio_path, _stt_prompt(report, tasks))
                store.set_input_text(input_id, text)
            else:
                store.insert_input(report_id, "text", text or "", question_id=question_id)
            answer = (text or "").strip()
            store.update_report(report_id, step="analýza")
            new_data = _apply_text(q, answer, data, tasks) if answer else data

        understood = new_data != data
        if understood:
            save_data(report_id, new_data)
            store.update_question(question_id, answer=answer[:1000])
        gaps = sync_questions(report_id, finish=False)

        after = store.get_question(question_id)
        assert after is not None
        if after["status"] == "open" and not understood:
            attempts = after["attempts"] + 1
            mode = "choices" if attempts >= CHOICES_AFTER else after["mode"]
            by_id = {t["id"]: t for t in tasks}
            store.update_question(
                question_id, attempts=attempts, mode=mode,
                text=question_text(_gap_of(after), attempts, new_data, by_id),
            )
        _finish(report_id, gaps)
    except Exception as exc:  # noqa: BLE001
        _finish_with_error(report_id, exc)


# --- odovzdanie a úpravy majiteľom ------------------------------------------------------


def submit(report_id: str) -> dict[str, Any]:
    from app.workforce.reports_pdf import render_worker_report

    report = store.get_report(report_id)
    if report is None:
        raise KeyError(report_id)
    if report["status"] in ("processing", "submitted"):
        raise ReportLocked(report["status"])
    gaps = sync_questions(report_id)
    if gaps:
        raise IncompleteReport(gaps)
    data = load_data(report)
    apply_report_statuses([e.model_dump() for e in data.tasks], report["day"])
    store.update_report(report_id, status="submitted",
                        submitted_at=datetime.now(UTC).isoformat(timespec="seconds"))
    render_worker_report(report_id)
    updated = store.get_report(report_id)
    assert updated is not None
    return updated


def owner_update(report_id: str, data: ReportData) -> dict[str, Any]:
    """Majiteľ opraví výpis (aj odovzdaný) — stav úloh a PDF sa prepočítajú."""
    from app.workforce.reports_pdf import render_worker_report

    report = store.get_report(report_id)
    if report is None:
        raise KeyError(report_id)
    if report["status"] == "processing":
        raise ReportLocked("processing")
    save_data(report_id, data)
    store.update_report(report_id, edited_by_owner=1)
    if report["status"] == "submitted":
        apply_report_statuses([e.model_dump() for e in data.tasks], report["day"])
        render_worker_report(report_id)
    else:
        sync_questions(report_id)
    updated = store.get_report(report_id)
    assert updated is not None
    return updated


def recover_stuck_reports() -> None:
    """Po reštarte servera: výpisy zaseknuté v „processing“ sa vrátia do dialógu."""
    for report in store.list_reports():
        if report["status"] == "processing":
            sync_questions(report["id"])
            store.update_report(report["id"], error="spracovanie prerušil reštart — skús znova")

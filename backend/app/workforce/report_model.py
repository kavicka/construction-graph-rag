"""Štruktúra denného výpisu a deterministické hľadanie chýbajúcich údajov."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.workforce.sk_numbers import parse_hhmm

TaskStatus = Literal["done", "in_progress", "not_started"]
STATUS_LABELS: dict[str, str] = {
    "done": "hotová",
    "in_progress": "rozpracovaná",
    "not_started": "nerobilo sa",
}


class TaskEntry(BaseModel):
    task_id: str
    status: TaskStatus | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    hours: float | None = Field(default=None, ge=0, le=24)
    note: str = Field(default="", max_length=500)


class ExtraWork(BaseModel):
    description: str = Field(min_length=1, max_length=300)
    hours: float | None = Field(default=None, ge=0, le=24)


class ReportData(BaseModel):
    absent: bool = False
    absence_reason: str = Field(default="", max_length=300)
    arrival: str | None = None  # "HH:MM"
    departure: str | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=300)
    tasks: list[TaskEntry] = Field(default_factory=list)
    extra_work: list[ExtraWork] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    problems_answered: bool = False
    summary: str = Field(default="", max_length=1000)

    def entry(self, task_id: str) -> TaskEntry | None:
        return next((e for e in self.tasks if e.task_id == task_id), None)

    def upsert_entry(self, task_id: str) -> TaskEntry:
        entry = self.entry(task_id)
        if entry is None:
            entry = TaskEntry(task_id=task_id)
            self.tasks.append(entry)
        return entry

    @property
    def worked_hours(self) -> float | None:
        start, end = parse_hhmm(self.arrival), parse_hhmm(self.departure)
        if start is None or end is None or self.break_minutes is None:
            return None
        minutes = (end[0] * 60 + end[1]) - (start[0] * 60 + start[1]) - self.break_minutes
        return round(minutes / 60, 2)

    @property
    def reported_hours(self) -> float:
        total = sum(e.hours or 0 for e in self.tasks if e.status != "not_started")
        return round(total + sum(x.hours or 0 for x in self.extra_work), 2)


GapKind = Literal["absence", "arrival", "departure", "break", "task", "extra", "problems",
                  "hours"]


@dataclass(frozen=True)
class Gap:
    key: str  # stabilný kľúč otázky (rovnaký kým chýba ten istý údaj)
    kind: GapKind
    task_id: str | None = None
    detail: str = ""  # čo presne chýba: "status" | "hours" | "progress" | "hours,progress" …
    extra_index: int | None = None


def find_gaps(
    data: ReportData, active_task_ids: list[str], *, tolerance: float
) -> list[Gap]:
    """Chýbajúce údaje v poradí, v akom sa na ne pýtame. Prázdny zoznam = dá sa odovzdať."""
    if data.absent:
        return [] if data.absence_reason.strip() else [Gap("absence", "absence")]

    gaps: list[Gap] = []
    if parse_hhmm(data.arrival) is None:
        gaps.append(Gap("arrival", "arrival"))
    if parse_hhmm(data.departure) is None:
        gaps.append(Gap("departure", "departure"))
    if data.break_minutes is None:
        gaps.append(Gap("break", "break"))

    for task_id in active_task_ids:
        entry = data.entry(task_id)
        if entry is None or entry.status is None:
            gaps.append(Gap(f"task:{task_id}", "task", task_id, "status"))
            continue
        missing: list[str] = []
        if entry.status == "not_started":
            if not entry.note.strip():
                missing.append("reason")
        else:
            if entry.hours is None:
                missing.append("hours")
            if entry.status == "in_progress" and entry.progress is None:
                missing.append("progress")
        if missing:
            gaps.append(Gap(f"task:{task_id}", "task", task_id, ",".join(missing)))

    for i, extra in enumerate(data.extra_work):
        if extra.hours is None:
            gaps.append(Gap(f"extra:{i}", "extra", detail="hours", extra_index=i))

    if not data.problems_answered:
        gaps.append(Gap("problems", "problems"))

    # súčet hodín má zmysel kontrolovať až keď je všetko ostatné vyplnené
    if not gaps:
        worked = data.worked_hours
        if worked is not None and worked <= 0:
            gaps.append(Gap("departure", "departure", detail="order"))
        elif worked is not None and abs(worked - data.reported_hours) > tolerance:
            gaps.append(Gap("hours", "hours"))
    return gaps


def entry_dict(entry: TaskEntry) -> dict[str, Any]:
    return entry.model_dump()

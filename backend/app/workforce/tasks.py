"""Task logic: which tasks are active on a given day, the board, status change from reports."""

from datetime import date
from typing import Any

from app.workforce import store

OPEN_STATUSES = ("todo", "in_progress")


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def is_active_on(task: dict[str, Any], day: date, by_id: dict[str, dict[str, Any]]) -> bool:
    """Active task = open, started (date or completed dependency) and not cancelled.

    The end of the deadline does NOT deactivate it — an overdue task is checked until it is done. A
    task completed on the same day stays active (a colleague may have worked on it too)."""
    if task["status"] == "cancelled":
        return False
    if task["status"] == "done":
        return task.get("done_on") == day.isoformat()
    start = _d(task.get("date_from"))
    if start and start > day:
        return False
    dep_id = task.get("depends_on")
    if dep_id:
        dep = by_id.get(dep_id) or store.get_task(dep_id)
        if dep is not None and dep["status"] != "cancelled":
            done_on = _d(dep.get("done_on"))
            if dep["status"] != "done" or done_on is None or done_on >= day:
                return False
    return True


def active_tasks_for(user_id: str, day: date) -> list[dict[str, Any]]:
    tasks = store.list_tasks(user_id=user_id)
    by_id = {t["id"]: t for t in tasks}
    return [t for t in tasks if is_active_on(t, day, by_id)]


def overlaps(task: dict[str, Any], start: date, end: date) -> bool:
    """The task falls within the period (on the board). An open task past its deadline always
    does."""
    t_from, t_to = _d(task.get("date_from")), _d(task.get("date_to"))
    if t_from and t_from > end:
        return False
    if t_to and t_to < start:
        return task["status"] in OPEN_STATUSES
    return True


def apply_report_statuses(entries: list[dict[str, Any]], day: str) -> None:
    """A submitted report advances task state: finished → done, in progress → completion %."""
    for entry in entries:
        task = store.get_task(entry["task_id"])
        if task is None or task["status"] == "cancelled":
            continue
        status = entry.get("status")
        if status == "done":
            store.update_task(task["id"], {"status": "done", "progress": 100, "done_on": day})
        elif status == "in_progress":
            fields: dict[str, Any] = {"status": "in_progress"}
            if entry.get("progress") is not None:
                fields["progress"] = max(0, min(99, int(entry["progress"])))
            if task["status"] == "done":
                fields["done_on"] = None
            store.update_task(task["id"], fields)

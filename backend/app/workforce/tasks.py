"""Logika úloh: ktoré úlohy sú v daný deň aktívne, nástenka, zmena stavu z výpisov."""

from datetime import date
from typing import Any

from app.workforce import store

OPEN_STATUSES = ("todo", "in_progress")


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def is_active_on(task: dict[str, Any], day: date, by_id: dict[str, dict[str, Any]]) -> bool:
    """Aktívna úloha = otvorená, začala (dátum alebo dokončená závislosť) a nie je zrušená.

    Koniec termínu ju NEdeaktivuje — oneskorená úloha sa kontroluje, kým nie je hotová.
    Úloha dokončená v ten istý deň ostáva aktívna (spolupracovník ju mohol robiť tiež)."""
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
    """Úloha zasahuje do obdobia (na nástenke). Otvorená úloha po termíne zasahuje vždy."""
    t_from, t_to = _d(task.get("date_from")), _d(task.get("date_to"))
    if t_from and t_from > end:
        return False
    if t_to and t_to < start:
        return task["status"] in OPEN_STATUSES
    return True


def apply_report_statuses(entries: list[dict[str, Any]], day: str) -> None:
    """Odovzdaný výpis posunie stav úloh: hotová → done, rozpracovaná → % hotovosti."""
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

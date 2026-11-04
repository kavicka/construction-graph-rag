"""Who may see what of the company data (decision from /ask 2026-10-08).

admin  — everything (even without login: local admin)
worker — only themselves: own tasks, attendance, hours, reports; on a shared task, colleagues' names
member — nothing (company data is for the owner)
"""

from dataclasses import dataclass, replace
from typing import Literal

from app.auth import current_scope
from app.workforce.qa.data import Snapshot
from app.workforce.qa.intents import FirmaQuery
from app.workforce.runtime import is_worker

MEMBER_DENIED = (
    "Firemné údaje o úlohách, dochádzke a odpracovaných hodinách sú dostupné len majiteľovi."
)
OTHERS_DENIED = "Môžeš sa pýtať len na svoje úlohy, dochádzku a hodiny — údaje kolegov nevidíš."
ADMIN_ONLY_INTENTS = {"missing_reports"}


@dataclass(frozen=True)
class Viewer:
    kind: Literal["admin", "worker", "member"]
    user_id: str | None

    @property
    def own_only(self) -> bool:
        return self.kind == "worker"


def viewer_from_scope() -> Viewer:
    scope = current_scope()
    if scope.unrestricted:
        return Viewer("admin", scope.user_id)
    if scope.user_id and is_worker(scope.user_id):
        return Viewer("worker", scope.user_id)
    return Viewer("member", scope.user_id)


@dataclass(frozen=True)
class Denied:
    message: str


def authorize(query: FirmaQuery, viewer: Viewer, snap: Snapshot) -> FirmaQuery | Denied:
    if viewer.kind == "admin":
        return query
    if viewer.kind != "worker" or viewer.user_id is None:
        return Denied(MEMBER_DENIED)
    me = snap.worker_by_id.get(viewer.user_id)
    if me is None:
        return Denied(MEMBER_DENIED)
    if any(w.id != me.id for w in query.workers):
        return Denied(OTHERS_DENIED)
    for _, candidates in query.ambiguous:
        if me not in candidates:
            return Denied(OTHERS_DENIED)
    if query.unknown_name:
        # same text as for a colleague — the worker can't tell who is on the team and who is not
        return Denied(OTHERS_DENIED)
    if ADMIN_ONLY_INTENTS & set(query.intents):
        return Denied(OTHERS_DENIED)
    # someone else's task behaves as if the question had not mentioned it — its existence
    # does not leak either
    tasks = [t for t in query.tasks if me.id in t.assignees]
    intents = query.intents
    if query.tasks and not tasks:
        intents = list(dict.fromkeys("worker_tasks" if i == "task_people" else i for i in intents))
    return replace(query, workers=[me], ambiguous=[], tasks=tasks, intents=intents)

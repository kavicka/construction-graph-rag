"""Kto čo smie vidieť z firemných údajov (rozhodnutie z /ask 2026-10-08).

admin  — všetko (aj bez prihlasovania: lokálny admin)
worker — len seba: svoje úlohy, dochádzku, hodiny, výpisy; pri spoločnej úlohe mená kolegov
member — nič (firemné údaje sú pre majiteľa)
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
OTHERS_DENIED = (
    "Môžeš sa pýtať len na svoje úlohy, dochádzku a hodiny — údaje kolegov nevidíš."
)
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
        # ten istý text ako pri kolegovi — pracovník sa nedozvie, kto v tíme je a kto nie
        return Denied(OTHERS_DENIED)
    if ADMIN_ONLY_INTENTS & set(query.intents):
        return Denied(OTHERS_DENIED)
    # cudzia úloha sa správa, akoby ju otázka nespomenula — ani jej existencia neunikne
    tasks = [t for t in query.tasks if me.id in t.assignees]
    intents = query.intents
    if query.tasks and not tasks:
        intents = list(dict.fromkeys(
            "worker_tasks" if i == "task_people" else i for i in intents))
    return replace(query, workers=[me], ambiguous=[], tasks=tasks, intents=intents)

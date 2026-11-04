"""Firma: PIN prihlásenie pracovníkov, pracovníci, profesie, stavby, úlohy (nástenka)."""

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from app.api.access import require_admin
from app.auth import SESSION_COOKIE, current_scope
from app.config import get_settings
from app.workforce import store
from app.workforce.runtime import invalidate_worker_cache
from app.workforce.tasks import active_tasks_for, overlaps
from app.workforce.workers import (
    PinError,
    check_pin,
    create_worker,
    create_worker_session,
    validate_pin,
    worker_out,
)

router = APIRouter()


# --- PIN prihlásenie --------------------------------------------------------------------


class WorkerPublic(BaseModel):
    id: str
    name: str


@router.get("/auth/workers")
async def login_workers() -> list[WorkerPublic]:
    """Mená pracovníkov pre prihlasovaciu obrazovku (bez ďalších údajov)."""
    if not get_settings().auth_enabled:
        return []
    rows = await run_in_threadpool(store.list_workers, active_only=True)
    return [WorkerPublic(id=r["id"], name=r["name"]) for r in rows]


class PinLoginIn(BaseModel):
    worker_id: str = Field(min_length=1, max_length=64)
    pin: str = Field(min_length=1, max_length=12)


def _attempts_left(n: int) -> str:
    if n == 1:
        return "zostáva posledný pokus"
    return f"zostávajú {n} pokusy" if 2 <= n <= 4 else f"zostáva {n} pokusov"


@router.post("/auth/pin")
async def pin_login(body: PinLoginIn, response: Response) -> WorkerPublic:
    result = await run_in_threadpool(check_pin, body.worker_id, body.pin)
    if result.locked:
        raise HTTPException(status_code=423, detail="príliš veľa pokusov — skús o chvíľu")
    if not result.ok:
        left = f" ({_attempts_left(result.attempts_left)})" if result.attempts_left else ""
        raise HTTPException(status_code=401, detail=f"nesprávny PIN{left}")
    token, max_age = await run_in_threadpool(create_worker_session, body.worker_id)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        max_age=max_age,
    )
    row = await run_in_threadpool(store.get_worker, body.worker_id)
    assert row is not None
    return WorkerPublic(id=row["id"], name=row["name"])


# --- pracovníci a profesie --------------------------------------------------------------


class WorkerOut(BaseModel):
    id: str
    name: str
    professions: list[str]
    active: bool
    locked: bool


class WorkerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    pin: str
    professions: list[str] = Field(default_factory=list, max_length=20)


class WorkerUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    professions: list[str] = Field(default_factory=list, max_length=20)
    active: bool = True


class PinIn(BaseModel):
    pin: str


def _clean_list(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


@router.get("/workers")
async def list_workers() -> list[WorkerOut]:
    require_admin()
    rows = await run_in_threadpool(store.list_workers)
    return [WorkerOut(**worker_out(r)) for r in rows]


@router.post("/workers", status_code=201)
async def add_worker(body: WorkerIn) -> WorkerOut:
    require_admin()
    try:
        out = await run_in_threadpool(
            create_worker, body.name.strip(), body.pin, _clean_list(body.professions)
        )
    except PinError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    invalidate_worker_cache()
    return WorkerOut(**out)


@router.put("/workers/{worker_id}")
async def edit_worker(worker_id: str, body: WorkerUpdate) -> WorkerOut:
    require_admin()
    if await run_in_threadpool(store.get_worker, worker_id) is None:
        raise HTTPException(status_code=404, detail="pracovník neexistuje")
    await run_in_threadpool(
        lambda: store.update_worker(
            worker_id,
            name=body.name.strip(),
            professions=_clean_list(body.professions),
            active=body.active,
        )
    )
    row = await run_in_threadpool(store.get_worker, worker_id)
    assert row is not None
    return WorkerOut(**worker_out(row))


@router.put("/workers/{worker_id}/pin", status_code=204)
async def reset_pin(worker_id: str, body: PinIn) -> None:
    from app.auth import hash_password

    require_admin()
    if await run_in_threadpool(store.get_worker, worker_id) is None:
        raise HTTPException(status_code=404, detail="pracovník neexistuje")
    try:
        validate_pin(body.pin)
    except PinError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await run_in_threadpool(store.set_worker_pin, worker_id, hash_password(body.pin))


@router.delete("/workers/{worker_id}", status_code=204)
async def remove_worker(worker_id: str) -> None:
    require_admin()
    if await run_in_threadpool(store.get_worker, worker_id) is None:
        raise HTTPException(status_code=404, detail="pracovník neexistuje")
    await run_in_threadpool(store.delete_worker, worker_id)
    invalidate_worker_cache()


class ProfessionsIn(BaseModel):
    names: list[str] = Field(max_length=100)


@router.get("/professions")
async def get_professions() -> list[str]:
    require_admin()
    return await run_in_threadpool(store.list_professions)


@router.put("/professions")
async def set_professions(body: ProfessionsIn) -> list[str]:
    require_admin()
    names = _clean_list(body.names)
    await run_in_threadpool(store.replace_professions, names)
    return names


# --- stavby -----------------------------------------------------------------------------


class SiteOut(BaseModel):
    id: str
    name: str
    address: str
    active: bool


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=200)
    active: bool = True


def _site_out(row: dict[str, Any]) -> SiteOut:
    return SiteOut(
        id=row["id"], name=row["name"], address=row["address"], active=bool(row["active"])
    )


@router.get("/sites")
async def list_sites() -> list[SiteOut]:
    require_admin()
    return [_site_out(r) for r in await run_in_threadpool(store.list_sites)]


@router.post("/sites", status_code=201)
async def add_site(body: SiteIn) -> SiteOut:
    require_admin()
    row = await run_in_threadpool(store.insert_site, body.name.strip(), body.address.strip())
    return _site_out(row)


@router.put("/sites/{site_id}")
async def edit_site(site_id: str, body: SiteIn) -> SiteOut:
    require_admin()
    if await run_in_threadpool(store.get_site, site_id) is None:
        raise HTTPException(status_code=404, detail="stavba neexistuje")
    await run_in_threadpool(
        lambda: store.update_site(
            site_id, name=body.name.strip(), address=body.address.strip(), active=body.active
        )
    )
    row = await run_in_threadpool(store.get_site, site_id)
    assert row is not None
    return _site_out(row)


@router.delete("/sites/{site_id}", status_code=204)
async def remove_site(site_id: str) -> None:
    require_admin()
    await run_in_threadpool(store.delete_site, site_id)


# --- úlohy ------------------------------------------------------------------------------

TaskStatus = Literal["todo", "in_progress", "done", "cancelled"]


class TaskOut(BaseModel):
    id: str
    site_id: str
    site_name: str
    title: str
    description: str
    work_type: str | None
    date_from: str | None
    date_to: str | None
    depends_on: str | None
    depends_on_title: str | None = None
    status: TaskStatus
    progress: int
    done_on: str | None
    assignees: list[str]
    source_id: str | None
    source_doc_id: str | None = None
    source_filename: str | None = None
    source_quote: str | None
    source_page: int | None
    blocked: bool = False  # čaká na nedokončenú závislosť


class TaskIn(BaseModel):
    site_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    work_type: str | None = Field(default=None, max_length=80)
    date_from: date | None = None
    date_to: date | None = None
    depends_on: str | None = None
    status: TaskStatus = "todo"
    progress: int = Field(default=0, ge=0, le=100)
    assignees: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("date_to")
    @classmethod
    def _order(cls, v: date | None, info: Any) -> date | None:
        start = info.data.get("date_from")
        if v and start and v < start:
            raise ValueError("termín je pred začiatkom")
        return v


class AssigneesIn(BaseModel):
    user_ids: list[str] = Field(max_length=30)


def _task_out(
    row: dict[str, Any], by_id: dict[str, dict[str, Any]], sources: dict[str, dict[str, Any]]
) -> TaskOut:
    dep = by_id.get(row["depends_on"] or "") if row.get("depends_on") else None
    if row.get("depends_on") and dep is None:
        dep = store.get_task(row["depends_on"])
    src = sources.get(row["source_id"] or "")
    return TaskOut(
        **{
            k: row[k]
            for k in TaskOut.model_fields
            if k in row
            and k not in ("depends_on_title", "blocked", "source_doc_id", "source_filename")
        },
        depends_on_title=dep["title"] if dep else None,
        blocked=bool(dep and dep["status"] not in ("done", "cancelled")),
        source_doc_id=src["doc_id"] if src else None,
        source_filename=src["filename"] if src else None,
    )


def _tasks_out(rows: list[dict[str, Any]]) -> list[TaskOut]:
    by_id = {r["id"]: r for r in store.list_tasks()}
    sources = {s["id"]: s for s in store.list_sources()}
    return [_task_out(r, by_id, sources) for r in rows]


async def _validate_task(body: TaskIn, task_id: str | None = None) -> None:
    if await run_in_threadpool(store.get_site, body.site_id) is None:
        raise HTTPException(status_code=422, detail="stavba neexistuje")
    workers = await run_in_threadpool(store.worker_ids)
    if unknown := [u for u in body.assignees if u not in workers]:
        raise HTTPException(status_code=422, detail=f"neznámi pracovníci: {unknown}")
    if body.depends_on:
        if body.depends_on == task_id:
            raise HTTPException(status_code=422, detail="úloha nemôže čakať sama na seba")
        if await run_in_threadpool(store.get_task, body.depends_on) is None:
            raise HTTPException(status_code=422, detail="závislosť neexistuje")
        # cyklus: A čaká na B, B (nepriamo) na A
        current: str | None = body.depends_on
        seen: set[str] = set()
        while current and current not in seen:
            if current == task_id:
                raise HTTPException(status_code=422, detail="závislosti tvoria cyklus")
            seen.add(current)
            dep = await run_in_threadpool(store.get_task, current)
            current = dep["depends_on"] if dep else None


def _task_fields(body: TaskIn) -> dict[str, Any]:
    done = body.status == "done"
    return {
        "site_id": body.site_id,
        "title": body.title.strip(),
        "description": body.description.strip(),
        "work_type": body.work_type,
        "date_from": body.date_from.isoformat() if body.date_from else None,
        "date_to": body.date_to.isoformat() if body.date_to else None,
        "depends_on": body.depends_on,
        "status": body.status,
        "progress": 100 if done else body.progress,
    }


@router.get("/tasks")
async def list_tasks(
    site_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    include_done: bool = False,
) -> list[TaskOut]:
    require_admin()
    rows = await run_in_threadpool(lambda: store.list_tasks(site_id=site_id))
    if date_from and date_to:
        rows = [r for r in rows if overlaps(r, date_from, date_to)]
    if not include_done:
        rows = [r for r in rows if r["status"] not in ("done", "cancelled")]
    return await run_in_threadpool(_tasks_out, rows)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    require_admin()
    row = await run_in_threadpool(store.get_task, task_id)
    if row is None:
        raise HTTPException(status_code=404, detail="úloha neexistuje")
    out = (await run_in_threadpool(_tasks_out, [row]))[0]
    history = await run_in_threadpool(store.task_history, task_id)
    return {**out.model_dump(), "history": history}


@router.post("/tasks", status_code=201)
async def add_task(body: TaskIn) -> TaskOut:
    require_admin()
    await _validate_task(body)
    fields = _task_fields(body)
    if body.status == "done":
        fields["done_on"] = date.today().isoformat()
    task_id = await run_in_threadpool(store.insert_task, fields, body.assignees)
    row = await run_in_threadpool(store.get_task, task_id)
    assert row is not None
    return (await run_in_threadpool(_tasks_out, [row]))[0]


@router.put("/tasks/{task_id}")
async def edit_task(task_id: str, body: TaskIn) -> TaskOut:
    require_admin()
    old = await run_in_threadpool(store.get_task, task_id)
    if old is None:
        raise HTTPException(status_code=404, detail="úloha neexistuje")
    await _validate_task(body, task_id)
    fields = _task_fields(body)
    if body.status == "done" and old["status"] != "done":
        fields["done_on"] = date.today().isoformat()
    elif body.status != "done":
        fields["done_on"] = None
    await run_in_threadpool(store.update_task, task_id, fields)
    await run_in_threadpool(store.set_task_assignees, task_id, body.assignees)
    row = await run_in_threadpool(store.get_task, task_id)
    assert row is not None
    return (await run_in_threadpool(_tasks_out, [row]))[0]


@router.put("/tasks/{task_id}/assignees")
async def set_assignees(task_id: str, body: AssigneesIn) -> TaskOut:
    """Nástenka: presun karty medzi stĺpcami pracovníkov."""
    require_admin()
    if await run_in_threadpool(store.get_task, task_id) is None:
        raise HTTPException(status_code=404, detail="úloha neexistuje")
    workers = await run_in_threadpool(store.worker_ids)
    if unknown := [u for u in body.user_ids if u not in workers]:
        raise HTTPException(status_code=422, detail=f"neznámi pracovníci: {unknown}")
    await run_in_threadpool(store.set_task_assignees, task_id, body.user_ids)
    row = await run_in_threadpool(store.get_task, task_id)
    assert row is not None
    return (await run_in_threadpool(_tasks_out, [row]))[0]


@router.delete("/tasks/{task_id}", status_code=204)
async def remove_task(task_id: str) -> None:
    require_admin()
    await run_in_threadpool(store.delete_task, task_id)


# --- pracovník: moje úlohy --------------------------------------------------------------


@router.get("/me/tasks")
async def my_tasks(day: date | None = None) -> list[TaskOut]:
    user_id = current_scope().user_id
    if user_id is None:
        return []
    rows = await run_in_threadpool(active_tasks_for, user_id, day or date.today())
    return await run_in_threadpool(_tasks_out, rows)


DONE_VISIBLE_DAYS = 14


class MyBoardOut(BaseModel):
    tasks: list[TaskOut]
    today_ids: list[str]  # úlohy, na ktoré sa pýta dnešný výpis


@router.get("/me/board")
async def my_board() -> MyBoardOut:
    """Pracovníkova nástenka (len na čítanie): jeho úlohy po stavoch, hotové 14 dní dozadu.
    Stav sa mení výpisom — pracovník ho nepresúva ručne, aby sa výpis nedal obísť."""
    user_id = current_scope().user_id
    if user_id is None:
        return MyBoardOut(tasks=[], today_ids=[])
    today = date.today()

    def load() -> MyBoardOut:
        rows = [
            t
            for t in store.list_tasks(user_id=user_id)
            if t["status"] != "cancelled"
            and not (
                t["status"] == "done"
                and t["done_on"]
                and (today - date.fromisoformat(t["done_on"])).days > DONE_VISIBLE_DAYS
            )
        ]
        active = {t["id"] for t in active_tasks_for(user_id, today)}
        return MyBoardOut(tasks=_tasks_out(rows), today_ids=sorted(active))

    return await run_in_threadpool(load)

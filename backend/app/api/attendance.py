"""Dochádzka: denný výpis hlasom/textom, doplňujúce otázky, odovzdanie, PDF výpisy.

Pracovník (PIN) vidí len svoj dnešný výpis. Majiteľ (admin) vidí všetky dni a výpisy,
môže výpis vyplniť za pracovníka (worker_id) a opraviť ho aj po odovzdaní.
Spracovanie hlasu a LLM beží na pozadí — klient sa pýta na stav (status = processing).
"""

import mimetypes
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.auth import current_scope
from app.workforce import dialog, speech, store
from app.workforce.config import get_workforce_settings
from app.workforce.dialog import ChoiceAnswer, IncompleteReport, ReportLocked
from app.workforce.report_model import ReportData
from app.workforce.reports_pdf import render_daily_summary, summary_context, summary_is_stale
from app.workforce.runtime import is_worker, schedule

router = APIRouter(prefix="/attendance")

MAX_AUDIO_BYTES = 50 * 1024 * 1024
AUDIO_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
             "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac", "audio/x-m4a": "m4a"}


def _is_admin() -> bool:
    return current_scope().unrestricted


def _subject(worker_id: str | None) -> str:
    """Za koho sa výpis robí: pracovník vždy sám za seba, majiteľ za zvoleného pracovníka."""
    scope = current_scope()
    if scope.user_id and is_worker(scope.user_id):
        return scope.user_id
    if not _is_admin():
        raise HTTPException(status_code=403, detail="dochádzka je pre pracovníkov")
    if not worker_id or store.get_worker(worker_id) is None:
        raise HTTPException(status_code=422, detail="vyber pracovníka")
    return worker_id


def _report_for_access(report_id: str) -> dict[str, Any]:
    report = store.get_report(report_id)
    scope = current_scope()
    if report is None or not (_is_admin() or report["user_id"] == scope.user_id):
        raise HTTPException(status_code=404, detail="výpis neexistuje")
    if not _is_admin() and report["day"] != date.today().isoformat():
        raise HTTPException(status_code=403, detail="starší výpis môže upraviť len majiteľ")
    return report


class QuestionOut(BaseModel):
    id: str
    key: str
    kind: str
    task_id: str | None
    detail: str
    text: str
    attempts: int
    mode: str
    status: str
    answer: str | None


class ReportTask(BaseModel):
    id: str
    title: str
    site_name: str
    description: str
    status: str
    progress: int


class InputOut(BaseModel):
    id: str
    kind: str
    text: str
    question_id: str | None
    has_audio: bool
    created_at: str


class ReportOut(BaseModel):
    id: str
    user_id: str
    worker_name: str
    day: str
    status: str
    step: str | None
    error: str | None
    transcript: str
    data: dict[str, Any]
    tasks: list[ReportTask]
    questions: list[QuestionOut]
    open_questions: int
    worked_hours: float | None
    reported_hours: float
    can_submit: bool
    submitted_at: str | None
    edited_by_owner: bool
    pdf_url: str | None
    inputs: list[InputOut]
    tts: bool


def report_out(report: dict[str, Any]) -> ReportOut:
    data = ReportData.model_validate_json(report["data_json"] or "{}")
    worker = store.get_worker(report["user_id"])
    questions = store.list_questions(report["id"])
    shown = [q for q in questions if q["status"] != "obsolete"]
    open_count = sum(q["status"] == "open" for q in questions)
    # hotová úloha, o ktorej pracovník nehovoril, do jeho výpisu nepatrí (pýtame sa len na otvorené)
    mentioned = {e.task_id for e in data.tasks}
    tasks = [t for t in dialog.report_tasks(report)
             if t["status"] != "done" or t["id"] in mentioned]
    return ReportOut(
        id=report["id"], user_id=report["user_id"],
        worker_name=worker["name"] if worker else "", day=report["day"],
        status=report["status"], step=report["step"], error=report["error"],
        transcript=report["transcript"], data=data.model_dump(),
        tasks=[ReportTask(id=t["id"], title=t["title"], site_name=t["site_name"],
                          description=t["description"], status=t["status"],
                          progress=t["progress"]) for t in tasks],
        questions=[QuestionOut(**{k: q[k] for k in QuestionOut.model_fields}) for q in shown],
        open_questions=open_count,
        worked_hours=data.worked_hours, reported_hours=data.reported_hours,
        can_submit=report["status"] == "ready",
        submitted_at=report["submitted_at"], edited_by_owner=bool(report["edited_by_owner"]),
        pdf_url=f"/api/attendance/reports/{report['id']}/pdf" if report["pdf_path"] else None,
        inputs=[InputOut(id=i["id"], kind=i["kind"], text=i["text"],
                         question_id=i["question_id"], has_audio=bool(i["audio_path"]),
                         created_at=i["created_at"]) for i in store.list_inputs(report["id"])],
        tts=speech.tts_available(),
    )


async def _out(report_id: str) -> ReportOut:
    report = await run_in_threadpool(store.get_report, report_id)
    assert report is not None
    return await run_in_threadpool(report_out, report)


@router.get("/today")
async def today(worker_id: str | None = None, day: date | None = None) -> ReportOut:
    user_id = await run_in_threadpool(_subject, worker_id)
    the_day = day if (day and _is_admin()) else date.today()
    report = await run_in_threadpool(store.get_or_create_report, user_id, the_day.isoformat())
    return await _out(report["id"])


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> ReportOut:
    report = await run_in_threadpool(_report_for_access, report_id)
    return await run_in_threadpool(report_out, report)


async def _save_audio(report_id: str, file: UploadFile) -> Path:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="prázdna nahrávka")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="nahrávka je nad 50 MB")
    ctype = (file.content_type or "").split(";")[0].strip()
    ext = AUDIO_EXT.get(ctype) or Path(file.filename or "").suffix.lstrip(".") or "webm"
    folder = get_workforce_settings().audio_dir / report_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{store.new_id()}.{ext[:5]}"
    path.write_bytes(data)
    return path


def _locked(exc: ReportLocked) -> HTTPException:
    return HTTPException(status_code=409, detail=f"výpis sa nedá meniť ({exc})")


@router.post("/reports/{report_id}/audio", status_code=202)
async def add_audio(report_id: str, file: UploadFile) -> ReportOut:
    await run_in_threadpool(_report_for_access, report_id)
    path = await _save_audio(report_id, file)
    try:
        await run_in_threadpool(dialog.begin_main_input, report_id)
    except ReportLocked as exc:
        path.unlink(missing_ok=True)
        raise _locked(exc) from exc
    schedule(dialog.process_main_input, report_id, audio_path=path)
    return await _out(report_id)


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)


@router.post("/reports/{report_id}/text", status_code=202)
async def add_text(report_id: str, body: TextIn) -> ReportOut:
    await run_in_threadpool(_report_for_access, report_id)
    try:
        await run_in_threadpool(lambda: dialog.begin_main_input(report_id, audio=False))
    except ReportLocked as exc:
        raise _locked(exc) from exc
    schedule(dialog.process_main_input, report_id, text=body.text)
    return await _out(report_id)


async def _begin_answer(report_id: str, question_id: str, *, audio: bool = False) -> None:
    await run_in_threadpool(_report_for_access, report_id)
    try:
        await run_in_threadpool(
            lambda: dialog.begin_answer(report_id, question_id, audio=audio)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="otázka neexistuje") from exc
    except ReportLocked as exc:
        raise _locked(exc) from exc


@router.post("/reports/{report_id}/questions/{question_id}/text", status_code=202)
async def answer_text(report_id: str, question_id: str, body: TextIn) -> ReportOut:
    await _begin_answer(report_id, question_id)
    schedule(dialog.process_answer, report_id, question_id, text=body.text)
    return await _out(report_id)


@router.post("/reports/{report_id}/questions/{question_id}/audio", status_code=202)
async def answer_audio(report_id: str, question_id: str, file: UploadFile) -> ReportOut:
    await run_in_threadpool(_report_for_access, report_id)
    path = await _save_audio(report_id, file)
    try:
        await _begin_answer(report_id, question_id, audio=True)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    schedule(dialog.process_answer, report_id, question_id, audio_path=path)
    return await _out(report_id)


@router.post("/reports/{report_id}/questions/{question_id}/choice", status_code=202)
async def answer_choice(report_id: str, question_id: str, body: ChoiceAnswer) -> ReportOut:
    await _begin_answer(report_id, question_id)
    schedule(dialog.process_answer, report_id, question_id, choice=body)
    return await _out(report_id)


@router.get("/questions/{question_id}/speech")
async def question_speech(question_id: str) -> FileResponse:
    q = await run_in_threadpool(store.get_question, question_id)
    if q is None:
        raise HTTPException(status_code=404, detail="otázka neexistuje")
    await run_in_threadpool(_report_for_access, q["report_id"])
    try:
        path = await run_in_threadpool(speech.synthesize, q["text"])
    except speech.SpeechError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/wav",
                        headers={"Cache-Control": "private, max-age=86400"})


@router.post("/reports/{report_id}/submit")
async def submit(report_id: str) -> ReportOut:
    await run_in_threadpool(_report_for_access, report_id)
    try:
        await run_in_threadpool(dialog.submit, report_id)
    except ReportLocked as exc:
        raise _locked(exc) from exc
    except IncompleteReport as exc:
        raise HTTPException(status_code=409, detail={
            "message": "najprv odpovedz na všetky otázky",
            "open": [g.key for g in exc.gaps],
        }) from exc
    return await _out(report_id)


@router.get("/reports/{report_id}/pdf")
async def report_pdf(report_id: str, download: bool = False) -> FileResponse:
    report = await run_in_threadpool(store.get_report, report_id)
    scope = current_scope()
    if report is None or not (_is_admin() or report["user_id"] == scope.user_id):
        raise HTTPException(status_code=404, detail="výpis neexistuje")
    if not report["pdf_path"] or not Path(report["pdf_path"]).exists():
        raise HTTPException(status_code=404, detail="PDF ešte nie je vygenerované")
    worker = await run_in_threadpool(store.get_worker, report["user_id"])
    slug = "".join(c if c.isalnum() else "-" for c in (worker["name"] if worker else "vypis"))
    return FileResponse(
        report["pdf_path"], media_type="application/pdf",
        filename=f"vypis-{slug.lower()}-{report['day']}.pdf",
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/inputs/{input_id}/audio")
async def input_audio(input_id: str) -> FileResponse:
    row = await run_in_threadpool(store.get_input, input_id)
    if row is None or not row["audio_path"]:
        raise HTTPException(status_code=404, detail="nahrávka neexistuje")
    report = await run_in_threadpool(store.get_report, row["report_id"])
    scope = current_scope()
    if report is None or not (_is_admin() or report["user_id"] == scope.user_id):
        raise HTTPException(status_code=404, detail="nahrávka neexistuje")
    path = Path(row["audio_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="nahrávka už bola zmazaná")
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media)


# --- majiteľ ----------------------------------------------------------------------------


def _require_admin() -> None:
    if not _is_admin():
        raise HTTPException(status_code=403, detail="len pre majiteľa")


class DayRow(BaseModel):
    worker_id: str
    worker_name: str
    report_id: str | None
    status: str  # missing | draft | processing | questioning | ready | submitted
    absent: bool
    arrival: str | None
    departure: str | None
    worked_hours: float | None
    problems: int
    open_questions: int
    pdf_url: str | None


class DayOut(BaseModel):
    day: str
    rows: list[DayRow]
    summary_url: str | None
    summary_stale: bool
    summary: dict[str, Any]


def _day(day: str) -> DayOut:
    reports = {r["user_id"]: r for r in store.list_reports(day=day)}
    rows = []
    workers = store.list_workers(active_only=True)
    known = {w["id"] for w in workers}
    workers += [w for uid in reports if uid not in known and (w := store.get_worker(uid))]
    for w in workers:
        r = reports.get(w["id"])
        data = ReportData.model_validate_json(r["data_json"] or "{}") if r else ReportData()
        rows.append(DayRow(
            worker_id=w["id"], worker_name=w["name"], report_id=r["id"] if r else None,
            status=r["status"] if r else "missing", absent=data.absent,
            arrival=data.arrival, departure=data.departure, worked_hours=data.worked_hours,
            problems=len(data.problems),
            open_questions=len(store.list_questions(r["id"], status="open")) if r else 0,
            pdf_url=f"/api/attendance/reports/{r['id']}/pdf" if r and r["pdf_path"] else None,
        ))
    summary = store.get_daily_summary(day)
    ctx = summary_context(day)
    return DayOut(
        day=day, rows=rows,
        summary_url=f"/api/attendance/days/{day}/summary.pdf" if summary else None,
        summary_stale=summary_is_stale(day),
        summary={k: ctx[k] for k in ("submitted_count", "worker_count", "total_hours",
                                     "done_count", "missing")}
        | {"problems": len(ctx["problems"])},
    )


@router.get("/days/{day}")
async def day_overview(day: date) -> DayOut:
    _require_admin()
    return await run_in_threadpool(_day, day.isoformat())


@router.post("/days/{day}/summary")
async def generate_summary(day: date) -> DayOut:
    _require_admin()
    await run_in_threadpool(render_daily_summary, day.isoformat())
    return await run_in_threadpool(_day, day.isoformat())


@router.get("/days/{day}/summary.pdf")
async def summary_pdf(day: date, download: bool = False) -> FileResponse:
    _require_admin()
    iso = day.isoformat()
    if await run_in_threadpool(summary_is_stale, iso):
        await run_in_threadpool(render_daily_summary, iso)
    row = await run_in_threadpool(store.get_daily_summary, iso)
    assert row is not None
    return FileResponse(
        row["pdf_path"], media_type="application/pdf", filename=f"denny-vypis-{iso}.pdf",
        content_disposition_type="attachment" if download else "inline",
    )


class OwnerEdit(BaseModel):
    data: ReportData


@router.put("/reports/{report_id}")
async def owner_edit(report_id: str, body: OwnerEdit) -> ReportOut:
    _require_admin()
    try:
        await run_in_threadpool(dialog.owner_update, report_id, body.data)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="výpis neexistuje") from exc
    except ReportLocked as exc:
        raise _locked(exc) from exc
    return await _out(report_id)

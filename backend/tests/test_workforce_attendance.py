"""Fázy 5–7: denný výpis — extrakcia s overením čísel, doplňujúce otázky, nejasné odpovede
→ tlačidlá, blokovanie odovzdania, stav úloh, PDF za pracovníka a súhrnný denný výpis."""

import time
import wave
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.workforce import store
from app.workforce.runtime import cleanup_audio, daily_tick
from tests.wf_fixtures import FakeLLM, login_admin, make_site, make_task, make_worker

TODAY = date.today().isoformat()

MAIN = (
    "Prišiel som o pol ôsmej. Do obeda sme murovali priečky, robil som na tom päť hodín, "
    "sú hotové asi na polovicu. Odišiel som o štvrtej."
)


def wait_done(client: TestClient, report_id: str, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        report = client.get(f"/api/attendance/reports/{report_id}").json()
        if report["status"] != "processing":
            return dict(report)
        time.sleep(0.02)
    raise AssertionError("spracovanie neskončilo")


def open_question(report: dict[str, Any], kind: str) -> dict[str, Any]:
    q = next((q for q in report["questions"] if q["status"] == "open" and q["kind"] == kind), None)
    assert q is not None, (kind, [x["kind"] for x in report["questions"]])
    return q


@pytest.fixture
def setup(client: TestClient) -> dict[str, str]:
    site = make_site(client, "Rovinka")
    jano = make_worker(client, "Jano Murár", professions=["murár"])
    t1 = make_task(client, site, "Murovanie priečok 2. NP", [jano])
    t2 = make_task(client, site, "Osadenie okien", [jano])
    make_task(client, site, "Budúca strecha", [jano], date_from="2099-01-01")
    return {"site": site, "jano": jano, "t1": t1["id"], "t2": t2["id"]}


def _start(client: TestClient, worker: str) -> dict[str, Any]:
    report = client.get("/api/attendance/today", params={"worker_id": worker}).json()
    assert report["status"] == "draft" and report["questions"] == []
    assert [t["title"] for t in report["tasks"]] == ["Murovanie priečok 2. NP", "Osadenie okien"]
    return dict(report)


def test_full_dialog(client: TestClient, fake_llm: FakeLLM, setup: dict[str, str]) -> None:
    report = _start(client, setup["jano"])
    rid = report["id"]
    fake_llm.push("_Extracted", {
        "arrival": "07:30", "departure": "04:00",
        "tasks": [
            {"ref": "T1", "status": "in_progress", "progress": 50, "hours": 5,
             "note": "murovanie priečok"},
            {"ref": "T2", "status": "done", "hours": 3},  # okná nespomenul → celé sa zahodí
            {"ref": "T9", "status": "done"},  # neexistujúca úloha
        ],
        "summary": "Murovanie priečok.",
    })
    assert client.post(f"/api/attendance/reports/{rid}/text", json={"text": MAIN}).status_code \
        == 202
    report = wait_done(client, rid)
    data = report["data"]
    assert (data["arrival"], data["departure"]) == ("07:30", "16:00")
    t1 = next(e for e in data["tasks"] if e["task_id"] == setup["t1"])
    assert (t1["status"], t1["progress"], t1["hours"]) == ("in_progress", 50, 5)
    assert all(e["task_id"] != setup["t2"] for e in data["tasks"])
    assert report["status"] == "questioning" and not report["can_submit"]
    kinds = [q["kind"] for q in report["questions"] if q["status"] == "open"]
    assert kinds == ["break", "task", "problems"]
    assert "Osadenie okien" in open_question(report, "task")["text"]

    # odovzdanie s otvorenými otázkami → 409
    resp = client.post(f"/api/attendance/reports/{rid}/submit")
    assert resp.status_code == 409 and set(resp.json()["detail"]["open"]) == {
        "break", f"task:{setup['t2']}", "problems"}

    # prestávka — deterministicky
    q = open_question(report, "break")
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "pol hodiny"})
    report = wait_done(client, rid)
    assert report["data"]["break_minutes"] == 30

    # nejasná odpoveď 2× → konkrétnejšia otázka → tlačidlá
    q = open_question(report, "task")
    assert q["detail"] == "status" and q["mode"] == "open"
    fake_llm.push("_XTaskAnswer", {"status": None})
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "no robil som tam niečo"})
    report = wait_done(client, rid)
    q1 = open_question(report, "task")
    assert q1["attempts"] == 1 and q1["mode"] == "open"
    fake_llm.push("_XTaskAnswer", {"status": None})
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "neviem"})
    report = wait_done(client, rid)
    q2 = open_question(report, "task")
    assert q2["attempts"] == 2 and q2["mode"] == "choices"
    assert "vyber" in q2["text"].lower()
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/choice",
                json={"status": "done", "hours": 1})
    report = wait_done(client, rid)

    # problémy cez LLM
    q = open_question(report, "problems")
    fake_llm.push("_XProblems", {"has_problems": True,
                                 "problems": ["Chýbalo lepidlo na obklad."]})
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "chýbalo lepidlo na obklad"})
    report = wait_done(client, rid)
    assert report["data"]["problems"] == ["Chýbalo lepidlo na obklad."]

    # 8 h odpracované, pri úlohách 6 h → otázka na hodiny
    assert report["worked_hours"] == 8 and report["reported_hours"] == 6
    q = open_question(report, "hours")
    assert "8 h" in q["text"] and "6 h" in q["text"]
    fake_llm.push("_XHoursFix", {"extra_work": [
        {"description": "Upratovanie staveniska", "hours": 2}]})
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "ešte dve hodiny som upratoval stavenisko"})
    report = wait_done(client, rid)
    assert report["data"]["extra_work"] == [{"description": "Upratovanie staveniska",
                                             "hours": 2.0}]
    assert report["status"] == "ready" and report["can_submit"] is True

    resp = client.post(f"/api/attendance/reports/{rid}/submit")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["status"] == "submitted" and report["pdf_url"]
    pdf = client.get(report["pdf_url"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    t1_row, t2_row = store.get_task(setup["t1"]), store.get_task(setup["t2"])
    assert t1_row and t1_row["status"] == "in_progress" and t1_row["progress"] == 50
    assert t2_row and t2_row["status"] == "done" and t2_row["done_on"] == TODAY
    assert client.post(f"/api/attendance/reports/{rid}/submit").status_code == 409
    assert client.post(f"/api/attendance/reports/{rid}/text",
                       json={"text": "ešte niečo"}).status_code == 409

    answered = [q for q in report["questions"] if q["status"] == "answered"]
    assert {q["kind"] for q in answered} >= {"break", "problems", "hours"}
    history = client.get(f"/api/tasks/{setup['t2']}").json()["history"]
    assert history[0]["entry"]["status"] == "done"

    # súhrnný denný výpis
    day = client.get(f"/api/attendance/days/{TODAY}").json()
    row = day["rows"][0]
    assert row["status"] == "submitted" and row["worked_hours"] == 8
    assert day["summary"]["submitted_count"] == 1 and day["summary"]["total_hours"] == 8
    assert day["summary"]["problems"] == 1 and day["summary_stale"] is True
    pdf = client.get(f"/api/attendance/days/{TODAY}/summary.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(f"/api/attendance/days/{TODAY}").json()["summary_stale"] is False


def test_hallucinated_times_dropped(client: TestClient, fake_llm: FakeLLM,
                                    setup: dict[str, str]) -> None:
    rid = _start(client, setup["jano"])["id"]
    # „pol ôsmej“ = 7:30; LLM tvrdí 8:30 a odchod, ktorý nezaznel
    fake_llm.push("_Extracted", {"arrival": "08:30", "departure": "17:00",
                                 "break_minutes": 45})
    client.post(f"/api/attendance/reports/{rid}/text",
                json={"text": "Prišiel som o pol ôsmej a murovali sme."})
    report = wait_done(client, rid)
    # 8:30 je klasický posun „pol ôsmej“ o hodinu → opravené na čas, ktorý v texte je
    assert report["data"]["arrival"] == "07:30"
    assert report["data"]["departure"] is None  # odchod vôbec nezaznel
    assert report["data"]["break_minutes"] is None
    q = open_question(report, "departure")
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "o pol štvrtej"})
    assert wait_done(client, rid)["data"]["departure"] == "15:30"


def test_absence_day(client: TestClient, fake_llm: FakeLLM, setup: dict[str, str]) -> None:
    rid = _start(client, setup["jano"])["id"]
    fake_llm.push("_Extracted", {"absent": True, "absence_reason": "Bol som u lekára."})
    client.post(f"/api/attendance/reports/{rid}/text",
                json={"text": "Dnes som nebol v práci, bol som u lekára."})
    report = wait_done(client, rid)
    assert report["status"] == "ready" and report["data"]["absent"] is True
    assert client.post(f"/api/attendance/reports/{rid}/submit").status_code == 200
    day = client.get(f"/api/attendance/days/{TODAY}").json()
    assert day["rows"][0]["absent"] is True


def test_audio_input_and_speech(client: TestClient, fake_llm: FakeLLM, setup: dict[str, str],
                                monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prompts: list[str | None] = []

    def fake_transcribe(path: Path, prompt: str | None = None) -> str:
        prompts.append(prompt)
        assert path.exists()
        return "Prišiel som o siedmej."

    monkeypatch.setattr("app.workforce.speech.transcribe", fake_transcribe)
    rid = _start(client, setup["jano"])["id"]
    fake_llm.push("_Extracted", {"arrival": "07:00"})
    resp = client.post(f"/api/attendance/reports/{rid}/audio",
                       files={"file": ("vypis.webm", b"\x1aE\xdf\xa3fake", "audio/webm")})
    assert resp.status_code == 202
    report = wait_done(client, rid)
    assert report["transcript"] == "Prišiel som o siedmej."
    assert report["data"]["arrival"] == "07:00"
    assert prompts and "Rovinka" in (prompts[0] or "") and "Osadenie okien" in (prompts[0] or "")
    audio_input = report["inputs"][0]
    assert audio_input["has_audio"] is True
    assert client.get(f"/api/attendance/inputs/{audio_input['id']}/audio").status_code == 200

    def fake_tts(text: str) -> Path:
        out = tmp_path / "q.wav"
        with wave.open(str(out), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 160)
        return out

    monkeypatch.setattr("app.workforce.speech.synthesize", fake_tts)
    q = open_question(report, "departure")
    speech = client.get(f"/api/attendance/questions/{q['id']}/speech")
    assert speech.status_code == 200 and speech.headers["content-type"] == "audio/wav"
    empty = client.post(f"/api/attendance/reports/{rid}/audio",
                        files={"file": ("x.webm", b"", "audio/webm")})
    assert empty.status_code == 422

    # retencia: staré nahrávky preč, prepis ostáva
    from datetime import UTC, datetime, timedelta
    assert cleanup_audio(datetime.now(UTC) + timedelta(days=31)) == 1
    assert client.get(f"/api/attendance/inputs/{audio_input['id']}/audio").status_code == 404
    assert store.get_report(rid)["transcript"]  # type: ignore[index]


def test_stt_failure_keeps_report(client: TestClient, fake_llm: FakeLLM,
                                  setup: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(path: Path, prompt: str | None = None) -> str:
        return ""

    monkeypatch.setattr("app.workforce.speech.transcribe", broken)
    rid = _start(client, setup["jano"])["id"]
    client.post(f"/api/attendance/reports/{rid}/audio",
                files={"file": ("ticho.webm", b"\x1aE\xdf\xa3", "audio/webm")})
    report = wait_done(client, rid)
    assert report["error"] and "reč" in report["error"]
    assert report["status"] in ("draft", "questioning")


def test_owner_edit_and_worker_isolation(secured: None, client: TestClient,
                                         fake_llm: FakeLLM) -> None:
    login_admin(client)
    site = make_site(client)
    jano = make_worker(client, "Jano", pin="1111")
    fero = make_worker(client, "Fero", pin="2222")
    make_task(client, site, "Murovanie", [jano])
    fero_report = client.get("/api/attendance/today", params={"worker_id": fero}).json()
    client.post("/api/auth/logout")

    client.post("/api/auth/pin", json={"worker_id": jano, "pin": "1111"})
    mine = client.get("/api/attendance/today", params={"worker_id": fero}).json()
    assert mine["user_id"] == jano  # worker_id pracovník nemôže podvrhnúť
    assert client.get(f"/api/attendance/reports/{fero_report['id']}").status_code == 404
    assert client.get(f"/api/attendance/days/{TODAY}").status_code == 403
    assert client.put(f"/api/attendance/reports/{mine['id']}",
                      json={"data": {}}).status_code == 403
    client.post("/api/auth/logout")

    login_admin(client)
    edited = client.put(f"/api/attendance/reports/{mine['id']}", json={"data": {
        "arrival": "07:00", "departure": "15:30", "break_minutes": 30,
        "tasks": [{"task_id": store.list_tasks(site_id=site)[0]["id"], "status": "done",
                   "hours": 8}],
        "problems_answered": True,
    }}).json()
    assert edited["edited_by_owner"] is True and edited["worked_hours"] == 8


def test_daily_tick(client: TestClient, monkeypatch: pytest.MonkeyPatch,
                    setup: dict[str, str]) -> None:
    from datetime import datetime

    from app.workforce.config import get_workforce_settings

    monkeypatch.setenv("RAG_DAILY_SUMMARY", "true")
    monkeypatch.setenv("RAG_DAILY_SUMMARY_TIME", "18:30")
    get_workforce_settings.cache_clear()
    wednesday = datetime(2026, 10, 7, 18, 0)
    assert daily_tick(wednesday) is False  # ešte nie je čas
    assert daily_tick(wednesday.replace(hour=19)) is True
    assert store.get_daily_summary("2026-10-07") is not None
    assert daily_tick(wednesday.replace(hour=20)) is False  # nič sa nezmenilo
    saturday = datetime(2026, 10, 10, 19, 0)
    assert daily_tick(saturday) is False  # víkend bez výpisov


def test_vague_task_answer_keeps_note_and_counts_attempt(
    client: TestClient, fake_llm: FakeLLM, setup: dict[str, str]
) -> None:
    rid = _start(client, setup["jano"])["id"]
    fake_llm.push("_Extracted", {"tasks": [
        {"ref": "T1", "status": "in_progress", "hours": 3, "note": "debnenie"}]})
    client.post(f"/api/attendance/reports/{rid}/text",
                json={"text": "Tri hodiny som pripravoval debnenie na murovanie priečok."})
    report = wait_done(client, rid)
    q = next(q for q in report["questions"] if q["task_id"] == setup["t1"])
    assert q["detail"] == "progress"
    # LLM z nejasnej odpovede vyrobí len poznámku — to nie je odpoveď
    fake_llm.push("_XTaskAnswer", {"status": "in_progress", "note": "Urobil niečo."})
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/text",
                json={"text": "no robil som tam niečo"})
    report = wait_done(client, rid)
    after = next(x for x in report["questions"] if x["id"] == q["id"])
    assert after["attempts"] == 1 and after["status"] == "open"
    entry = next(e for e in report["data"]["tasks"] if e["task_id"] == setup["t1"])
    assert entry["note"] == "debnenie"


def test_rules_from_real_model_runs() -> None:
    """Poučenia z behov s qwen3/Whisper — časy „pol X“ posunuté o hodinu, skloňované názvy
    úloh, neprítomnosť, ktorú LLM prehliadol."""
    from app.workforce.extract import _time, absence_reason, task_mentioned

    assert _time("07:30", "prišiel som o pol siedmej", afternoon=False) == "06:30"
    assert _time("16:30", "odišiel som o pol štvrtej", afternoon=True) == "15:30"
    assert _time("08:30", "prišiel som o siedmej", afternoon=False) is None  # nič na opravu
    assert task_mentioned("Osadenie okenných rámov", "okná neprišli, rámy sa nerobili")
    assert task_mentioned("Betonáž základovej dosky", "debnenie na základovú dosku")
    assert not task_mentioned("Osadenie okenných rámov", "murovali sme priečky celý deň")
    assert absence_reason("Dnes som nebol v práci, mal som dovolenku.") == "Dovolenka"
    assert absence_reason("Murovali sme priečky osem hodín.") is None


def test_absence_fallback_and_escape_hatch(
    client: TestClient, fake_llm: FakeLLM, setup: dict[str, str]
) -> None:
    # LLM zlyhá → deterministická záloha spozná neprítomnosť
    rid = _start(client, setup["jano"])["id"]
    fake_llm.push("_Extracted", None)
    client.post(f"/api/attendance/reports/{rid}/text",
                json={"text": "Dnes som nebol v práci, bol som u lekára."})
    report = wait_done(client, rid)
    assert report["data"]["absent"] is True and report["data"]["absence_reason"] == "Lekár"
    assert report["status"] == "ready"


def test_absence_choice_on_time_question(
    client: TestClient, fake_llm: FakeLLM, setup: dict[str, str]
) -> None:
    rid = _start(client, setup["jano"])["id"]
    fake_llm.push("_Extracted", {})
    client.post(f"/api/attendance/reports/{rid}/text", json={"text": "hmm"})
    report = wait_done(client, rid)
    q = open_question(report, "arrival")
    client.post(f"/api/attendance/reports/{rid}/questions/{q['id']}/choice",
                json={"absence_reason": "Dovolenka"})
    report = wait_done(client, rid)
    assert report["data"]["absent"] is True and report["status"] == "ready"


def test_two_times_fallback_and_done_task_not_asked(
    client: TestClient, fake_llm: FakeLLM, setup: dict[str, str]
) -> None:
    from app.workforce.extract import _times_from_text
    from app.workforce.report_model import ReportData

    data = ReportData()
    _times_from_text(data, "Prišiel som o 7. Odišiel som o pol štvrtej.")
    assert (data.arrival, data.departure) == ("07:00", "15:30")
    # úloha uzavretá dnes (napr. majiteľom) sa v dnešnom výpise nepýta
    store.update_task(setup["t2"], {"status": "done", "progress": 100, "done_on": TODAY})
    rid = _start_any(client, setup["jano"])
    fake_llm.push("_Extracted", {"arrival": "07:00", "departure": "15:30", "break_minutes": 30,
                                 "problems_mentioned": True})
    client.post(f"/api/attendance/reports/{rid}/text", json={
        "text": "Od siedmej do pol štvrtej, prestávka pol hodiny, žiadne problémy."})
    report = wait_done(client, rid)
    asked = {q["task_id"] for q in report["questions"] if q["status"] == "open"}
    assert setup["t2"] not in asked and setup["t1"] in asked


def _start_any(client: TestClient, worker: str) -> str:
    return str(client.get("/api/attendance/today", params={"worker_id": worker}).json()["id"])


def test_summary_sentences_with_invented_numbers_dropped() -> None:
    from app.workforce.extract import clean_summary

    said = ("Začal som o pol siedmej. Viazali sme výstuž stropu nad 4. podlažím, osem hodín, "
            "je hotová na 70%. Skončil som o pol piatej.")
    llm = ("Dnes sme viazali výstuž stropu nad 4. podlažím, práca bola na 70 % hotová. "
           "Pracoval som 8 hodín, začal som o 7:30, skončil o 17:30.")
    assert clean_summary(llm, said) == (
        "Dnes sme viazali výstuž stropu nad 4. podlažím, práca bola na 70 % hotová.")

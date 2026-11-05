"""Fill a running demo backend with synthetic data — everything goes through the HTTP API, the
same paths the UI uses. Called by scripts/demo.sh; every step skips work that is already done.

    uv run --project backend python backend/scripts/demo_seed.py \\
        --api http://localhost:8010 --data-dir demo-data --email … --password …

1. library   the 12-document construction corpus from the eval (eval/construction.py)
2. company   Stavby Kráľ: workers with fixed demo PINs, sites, tasks from minutes via the AI
             import, proposals approved and applied
3. reports   today's attendance reports typed as text (one worker absent) + the daily summary
4. chats     two questions in the library chat

All names are fictional. The PINs and the admin password are demo-only.
"""

import argparse
import json
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_firma  # noqa: E402

# fixed so the README can name them; the data is fake
DEMO_PINS = {name: str(1111 * (i + 1)) for i, (name, _) in enumerate(seed_firma.WORKERS)}
ABSENT_WORKER = "Martin Krajči"
# the owner's pick for proposals the AI left without an assignee (keyword in the title → worker)
OWNER_PICKS = [
    (("hydroizol",), "Michal Ondruš"),
    (("injekt", "murov"), "Marek Hudák"),
    (("prípojk", "rozvádz", "elektr"), "Lukáš Sloboda"),
    (("sadrokart", "priečk"), "Martin Krajči"),
    (("lešen",), "Rastislav Ďurica"),
    (("debnen", "betón", "rámp", "podlah"), "Tomáš Baláž"),
]
MAX_REPORTS = 3  # workers who report today (besides the absent one) — each costs LLM calls
CHAT_QUESTIONS = [
    "Na ktorej stavbe je nasadený žeriav TC-03 a kto je tam stavbyvedúci?",
    "Which warranty claims are still open?",
]
TIMINGS: list[tuple[str, float]] = []


@contextmanager
def stage(name: str) -> Iterator[None]:
    print(f"\n== {name}", flush=True)
    t0 = time.time()
    yield
    took = time.time() - t0
    TIMINGS.append((name, took))
    print(f"== {name}: {took:.0f} s", flush=True)


def wait_for_backend(api: str, timeout_s: float = 900) -> None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            if httpx.get(f"{api}/api/health", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise SystemExit(f"backend at {api} did not come up in {timeout_s:.0f} s")


# --- 1. library ----------------------------------------------------------------------------


def seed_library(client: httpx.Client, data_dir: Path) -> None:
    from eval.construction import generate_construction_corpus

    pdfs = generate_construction_corpus(data_dir / "corpus")
    have = {d["filename"] for d in client.get("/documents").json()}
    missing = [p for p in pdfs if p.name not in have]
    if missing:
        files = [("files", (p.name, p.read_bytes(), "application/pdf")) for p in missing]
        client.post("/documents", files=files).raise_for_status()
        print(f"uploaded {len(missing)} documents, indexing…")
    names = {p.name for p in pdfs}
    last = ""
    while True:
        docs = [d for d in client.get("/documents").json() if d["filename"] in names]
        done = sum(d["status"] == "done" for d in docs)
        failed = [d["filename"] for d in docs if d["status"] == "error"]
        line = f"  indexed {done}/{len(names)}" + (f", failed: {failed}" if failed else "")
        if line != last:
            print(line, flush=True)
            last = line
        if done + len(failed) == len(names):
            return
        time.sleep(3)


# --- 2. company ----------------------------------------------------------------------------


def _put_draft(
    client: httpx.Client,
    source_id: str,
    draft: dict[str, Any],
    decision: str,
    assignees: list[str] | None = None,
) -> None:
    x = draft["data"]
    client.put(
        f"/task-imports/{source_id}/drafts/{draft['id']}",
        json={
            "title": x["title"],
            "description": x.get("description") or "",
            "work_type": x.get("work_type"),
            "date_from": x.get("date_from"),
            "date_to": x.get("date_to"),
            "assignees": (x.get("assignees") or []) if assignees is None else assignees,
            "decision": decision,
        },
    ).raise_for_status()


def _owner_assignee(title: str, workers: dict[str, str]) -> str | None:
    """Who the owner would pick for a proposal the AI left without anyone."""
    low = title.lower()
    for keys, name in OWNER_PICKS:
        if any(k in low for k in keys) and name in workers:
            return workers[name]
    return None


def _false_duplicate(data: dict[str, Any]) -> bool:
    """A "possible duplicate" flag the owner would dismiss: the titles are clearly different
    (on the low RAM profile the small embedding model rates most task titles as similar)."""
    from rapidfuzz import fuzz

    dup = data.get("duplicate_of")
    if not dup:
        return False
    return fuzz.ratio(data["title"].lower(), str(dup["title"]).lower()) < 85


def approve_and_apply(client: httpx.Client, source_id: str) -> dict[str, int]:
    """What the owner does on the review page: fill in missing assignees, dismiss false duplicate
    flags, „Schváliť všetko“ (approve all), reject what would need a manual decision (no deadline,
    real duplicate, depends on a rejected proposal), then apply."""
    workers = {w["name"]: w["id"] for w in client.get("/workers").json()}
    for draft in client.get(f"/task-imports/{source_id}").json()["drafts"]:
        data = draft["data"]
        if draft["kind"] != "new" or draft["decision"] != "pending":
            continue
        who = None if data.get("assignees") else _owner_assignee(data["title"], workers)
        dismiss = _false_duplicate(data) and data.get("date_to")
        if who or dismiss:
            _put_draft(
                client,
                source_id,
                draft,
                "approved" if dismiss else "pending",
                [who] if who else None,
            )
    client.post(f"/task-imports/{source_id}/decide-all", json={"decision": "approved"})
    client.post(f"/task-imports/{source_id}/decide-all", json={"decision": "rejected"})
    for _ in range(5):
        resp = client.post(f"/task-imports/{source_id}/apply")
        if resp.status_code != 409:
            resp.raise_for_status()
            return dict(resp.json())
        drafts = {d["id"]: d for d in client.get(f"/task-imports/{source_id}").json()["drafts"]}
        for problem in resp.json()["detail"]["problems"]:
            if draft := drafts.get(problem.get("draft_id", "")):
                _put_draft(client, source_id, draft, "rejected")
    raise SystemExit(f"import {source_id} could not be applied")


def seed_company(client: httpx.Client, data_dir: Path) -> None:
    created = seed_firma.seed_workers(client, DEMO_PINS)
    print(f"workers: {len(created)} new")
    sites = seed_firma.seed_sites(client)
    print("sites:", ", ".join(sites))
    # minutes dated two days ago → their deadlines fall in the coming days
    base = date.today() - timedelta(days=2)

    def apply_now(detail: dict[str, Any]) -> None:
        seed_firma.print_drafts(client, detail)
        if detail["source"]["status"] == "review":
            counts = approve_and_apply(client, detail["source"]["id"])
            print(f"  applied: {counts}", flush=True)

    seed_firma.seed_imports(client, sites, data_dir / "seed-docs", base, after_review=apply_now)
    # an import left in review by an interrupted earlier run
    for source in client.get("/task-imports").json():
        if source["status"] == "review":
            print(f"applied {source['filename']}: {approve_and_apply(client, source['id'])}")
    print(f"tasks: {len(client.get('/tasks').json())}")


# --- 3. attendance reports -----------------------------------------------------------------


def _wait_report(client: httpx.Client, report_id: str) -> dict[str, Any]:
    while True:
        report: dict[str, Any] = client.get(f"/attendance/reports/{report_id}").json()
        if report["status"] != "processing":
            return report
        time.sleep(2)


def _report_text(tasks: list[dict[str, Any]], with_problem: bool) -> str:
    """A worker's typed report: 7:00–15:30 with a 30 min break = 8 h split across the tasks."""
    split = {1: [8], 2: [5, 3]}.get(len(tasks), [4, 2, 2])
    parts = ["Ráno som prišiel o siedmej, odchádzam o pol štvrtej, obed bol pol hodiny."]
    for task, hours in zip(tasks, split, strict=False):
        unit = "hodiny" if hours < 5 else "hodín"
        parts.append(
            f"Na úlohe {task['title']} som robil {hours} {unit}, je hotová asi na polovicu."
        )
    for task in tasks[len(split) :]:
        parts.append(f"Na úlohe {task['title']} sa dnes nerobilo, čakáme na materiál.")
    parts.append(
        "Problém: meškala dodávka betónu, hodinu sme čakali."
        if with_problem
        else "Žiadne problémy neboli."
    )
    return " ".join(parts)


def _choice_for(question: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """What a worker would tap on the follow-up buttons."""
    kind = question["kind"]
    if kind == "arrival":
        return {"time": "07:00"}
    if kind == "departure":
        return {"time": "15:30"}
    if kind == "break":
        return {"minutes": 30}
    if kind == "absence":
        return {"absence_reason": "choroba"}
    if kind == "task":
        return {"status": "in_progress", "hours": 2, "progress": 50}
    if kind == "extra":
        return {"hours": 1}
    if kind == "problems":
        return {"problems": []}
    # hours: spread the worked time evenly over the tasks that were worked on
    data = report["data"]
    worked = (report["worked_hours"] or 8) - sum(x["hours"] or 0 for x in data["extra_work"])
    ids = [e["task_id"] for e in data["tasks"] if e["status"] != "not_started"]
    share = round(worked / max(len(ids), 1) * 2) / 2
    hours = {tid: share for tid in ids}
    if ids:
        hours[ids[-1]] = round(worked - share * (len(ids) - 1), 2)
    return {"task_hours": hours}


def file_report(client: httpx.Client, report: dict[str, Any], text: str) -> str:
    rid = report["id"]
    client.post(f"/attendance/reports/{rid}/text", json={"text": text}).raise_for_status()
    report = _wait_report(client, rid)
    for _ in range(12):
        if report["status"] != "questioning":
            break
        q = next(q for q in report["questions"] if q["status"] == "open")
        print(f"    follow-up ({q['kind']}): {q['text']}")
        client.post(
            f"/attendance/reports/{rid}/questions/{q['id']}/choice", json=_choice_for(q, report)
        ).raise_for_status()
        report = _wait_report(client, rid)
    if report["status"] == "ready":
        client.post(f"/attendance/reports/{rid}/submit").raise_for_status()
        return "submitted"
    return str(report["status"])


def seed_reports(client: httpx.Client) -> None:
    today = date.today().isoformat()
    reported = 0
    for worker in client.get("/workers").json():
        report = client.get("/attendance/today", params={"worker_id": worker["id"]}).json()
        open_tasks = [t for t in report["tasks"] if t["status"] != "done"]
        if report["status"] != "draft" or report["inputs"]:
            reported += worker["name"] != ABSENT_WORKER
            continue  # already reported
        if worker["name"] == ABSENT_WORKER:
            text = "Dnes som nebol v práci, som chorý, bol som u lekára."
        elif open_tasks and reported < MAX_REPORTS:
            text = _report_text(open_tasks, with_problem=reported == 0)
            reported += 1
        else:
            continue
        t0 = time.time()
        print(f"  {worker['name']}: {text}")
        result = file_report(client, report, text)
        print(f"  → {result} in {time.time() - t0:.0f} s")
    client.post(f"/attendance/days/{today}/summary").raise_for_status()
    day = client.get(f"/attendance/days/{today}").json()
    print(f"daily summary {today}: {json.dumps(day.get('summary'), ensure_ascii=False)}")


# --- 4. chats ------------------------------------------------------------------------------


def seed_chats(client: httpx.Client) -> None:
    asked = {c["title"] for c in client.get("/chats").json()}
    for question in CHAT_QUESTIONS:
        if question[:60] in asked:
            continue
        t0 = time.time()
        answer = ""
        with client.stream("POST", "/chat", json={"message": question}, timeout=600) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event["type"] == "delta":
                    answer += event.get("text", "")
                elif event["type"] == "error":
                    print(f"  chat error: {event['message']}")
        print(f"  Q: {question}\n  A: {answer.strip()[:300]}  ({time.time() - t0:.0f} s)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8010")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if data_dir == (ROOT / "data").resolve():
        raise SystemExit("refusing to seed demo data into data/ — use a separate --data-dir")

    with stage("backend up"):
        wait_for_backend(args.api)
    client = seed_firma.login(args.api, args.email, args.password)
    with stage("library (12 synthetic PDFs)"):
        seed_library(client, data_dir)
    with stage("company (workers, sites, AI task import)"):
        seed_company(client, data_dir)
    with stage("attendance reports + daily summary"):
        seed_reports(client)
    with stage("chats"):
        seed_chats(client)

    print("\nstage timings:")
    for name, took in TIMINGS:
        print(f"  {name:<45} {took:6.0f} s")


if __name__ == "__main__":
    main()

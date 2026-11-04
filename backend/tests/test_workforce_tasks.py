"""Fáza 2: stavby, úlohy, priradenia (nástenka), závislosti, aktívne úlohy dňa."""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.workforce import store
from app.workforce.tasks import active_tasks_for, apply_report_statuses, overlaps
from tests.wf_fixtures import make_site, make_task, make_worker

TODAY = date.today()


def _iso(d: date) -> str:
    return d.isoformat()


def test_sites_crud(client: TestClient) -> None:
    sid = make_site(client, "Rovinka")
    make_site(client, "Bernolákovo")
    names = [s["name"] for s in client.get("/api/sites").json()]
    assert names == ["Bernolákovo", "Rovinka"]
    resp = client.put(
        f"/api/sites/{sid}", json={"name": "Rovinka RD", "address": "Hlavná 1", "active": False}
    )
    assert resp.json() == {"id": sid, "name": "Rovinka RD", "address": "Hlavná 1", "active": False}
    assert client.delete(f"/api/sites/{sid}").status_code == 204
    assert len(client.get("/api/sites").json()) == 1


def test_task_validation(client: TestClient) -> None:
    sid = make_site(client)
    jano = make_worker(client, "Jano")
    bad_site = client.post("/api/tasks", json={"site_id": "x", "title": "A"})
    assert bad_site.status_code == 422
    bad_worker = client.post(
        "/api/tasks", json={"site_id": sid, "title": "A", "assignees": ["nikto"]}
    )
    assert bad_worker.status_code == 422
    bad_dates = client.post(
        "/api/tasks",
        json={
            "site_id": sid,
            "title": "A",
            "date_from": _iso(TODAY),
            "date_to": _iso(TODAY - timedelta(1)),
        },
    )
    assert bad_dates.status_code == 422

    a = make_task(client, sid, "Základy", [jano])
    b = make_task(client, sid, "Murovanie", [jano], depends_on=a["id"])
    assert b["depends_on_title"] == "Základy" and b["blocked"] is True
    # cyklus A → B → A
    cyc = client.put(
        f"/api/tasks/{a['id']}",
        json={"site_id": sid, "title": "Základy", "assignees": [jano], "depends_on": b["id"]},
    )
    assert cyc.status_code == 422 and "cyklus" in cyc.json()["detail"]
    self_dep = client.put(
        f"/api/tasks/{a['id']}", json={"site_id": sid, "title": "Základy", "depends_on": a["id"]}
    )
    assert self_dep.status_code == 422


def test_board_move_and_filters(client: TestClient) -> None:
    sid = make_site(client, "Rovinka")
    other = make_site(client, "Pezinok")
    jano, fero = make_worker(client, "Jano"), make_worker(client, "Fero")
    t = make_task(
        client, sid, "Priečky", [jano], date_from=_iso(TODAY), date_to=_iso(TODAY + timedelta(3))
    )
    make_task(
        client,
        other,
        "Strecha",
        [fero],
        date_from=_iso(TODAY + timedelta(30)),
        date_to=_iso(TODAY + timedelta(35)),
    )
    make_task(client, sid, "Hotová vec", [jano], status="done")

    # presun Jano → Fero, potom Alt+drag pridá Jana späť
    moved = client.put(f"/api/tasks/{t['id']}/assignees", json={"user_ids": [fero]}).json()
    assert moved["assignees"] == [fero]
    both = client.put(f"/api/tasks/{t['id']}/assignees", json={"user_ids": [fero, jano]}).json()
    assert sorted(both["assignees"]) == sorted([fero, jano])
    unassigned = client.put(f"/api/tasks/{t['id']}/assignees", json={"user_ids": []}).json()
    assert unassigned["assignees"] == []

    week = {"date_from": _iso(TODAY), "date_to": _iso(TODAY + timedelta(6))}
    titles = {x["title"] for x in client.get("/api/tasks", params=week).json()}
    assert titles == {"Priečky"}
    with_done = client.get("/api/tasks", params={**week, "include_done": True}).json()
    assert {x["title"] for x in with_done} == {"Priečky", "Hotová vec"}
    by_site = client.get("/api/tasks", params={"site_id": other}).json()
    assert [x["title"] for x in by_site] == ["Strecha"]

    edited = client.put(
        f"/api/tasks/{t['id']}",
        json={
            "site_id": sid,
            "title": "Priečky 2. NP",
            "status": "done",
            "assignees": [jano],
        },
    ).json()
    assert edited["status"] == "done" and edited["progress"] == 100
    assert edited["done_on"] == _iso(TODAY)
    detail = client.get(f"/api/tasks/{t['id']}").json()
    assert detail["history"] == []
    assert client.delete(f"/api/tasks/{t['id']}").status_code == 204


def test_active_tasks_rules(client: TestClient) -> None:
    sid = make_site(client)
    jano = make_worker(client, "Jano")
    future = make_task(client, sid, "Budúca", [jano], date_from=_iso(TODAY + timedelta(2)))
    overdue = make_task(
        client,
        sid,
        "Po termíne",
        [jano],
        date_from=_iso(TODAY - timedelta(9)),
        date_to=_iso(TODAY - timedelta(2)),
    )
    base = make_task(client, sid, "Základy", [jano])
    after = make_task(client, sid, "Murovanie", [jano], depends_on=base["id"])
    cancelled = make_task(client, sid, "Zrušená", [jano], status="cancelled")

    active = {t["title"] for t in active_tasks_for(jano, TODAY)}
    assert active == {"Po termíne", "Základy"}
    assert future["id"] and cancelled["id"]

    # závislosť dokončená dnes → nadväzujúca úloha začína až zajtra
    apply_report_statuses([{"task_id": base["id"], "status": "done"}], _iso(TODAY))
    today_active = {t["title"] for t in active_tasks_for(jano, TODAY)}
    assert today_active == {"Po termíne", "Základy"}  # hotová dnes ostáva v dnešnom výpise
    tomorrow = {t["title"] for t in active_tasks_for(jano, TODAY + timedelta(1))}
    assert tomorrow == {"Po termíne", "Murovanie"}
    assert client.get("/api/me/tasks").json() == []  # bez prihlásenia nie je „ja“

    apply_report_statuses(
        [{"task_id": overdue["id"], "status": "in_progress", "progress": 60}], _iso(TODAY)
    )
    row = store.get_task(overdue["id"])
    assert row is not None and row["status"] == "in_progress" and row["progress"] == 60
    assert after["blocked"] is True


def test_overlaps() -> None:
    task = {"date_from": "2026-10-10", "date_to": "2026-10-12", "status": "todo"}
    assert overlaps(task, date(2026, 10, 12), date(2026, 10, 20))
    assert overlaps(task, date(2026, 10, 13), date(2026, 10, 20))  # po termíne, ale otvorená
    assert not overlaps({**task, "status": "done"}, date(2026, 10, 13), date(2026, 10, 20))
    assert not overlaps(task, date(2026, 10, 1), date(2026, 10, 9))

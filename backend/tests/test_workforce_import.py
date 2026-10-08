"""Fázy 3–4: import úloh z dokumentu — citáty, termíny, závislosti, návrh pracovníka,
schválenie, nová verzia dokumentu (nové / zmenené / zrušené / bez zmeny)."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import db
from app.workforce import importer, store
from app.workforce.assign import match_person, suggest_worker
from app.workforce.importer import Segment, make_windows, run_import, verify_quote
from tests.wf_fixtures import FakeLLM, make_site, make_worker

EMAIL = (
    "Dobrý deň, posielam rozpis prác na RD Rovinka. Do 20. 10. 2026 treba dokončiť betonáž "
    "základovej dosky. Po dokončení základov murovanie obvodových stien, termín do konca "
    "mesiaca. Elektroinštaláciu v 1. NP urobí Peter Kováč 2.–6. 11. 2026. "
    "Faktúru pošlite do 14 dní. S pozdravom, Ing. Novák, 5. 10. 2026"
)


def _task(title: str, quote: str, **kw: Any) -> dict[str, Any]:
    return {"title": title, "quote": quote, "description": "", "work_type": "iné", **kw}


def _seed_source(client: TestClient, site_id: str, monkeypatch: pytest.MonkeyPatch,
                 text: str = EMAIL, previous_id: str | None = None,
                 filename: str = "rozpis.pdf") -> str:
    doc_id = store.new_id()
    db.insert_document(doc_id, filename, f"sha-{doc_id}")
    db.set_document_status(doc_id, "done", progress=1.0)
    monkeypatch.setattr(importer, "document_segments", lambda _doc: [Segment(text, 1)])
    version = 1
    if previous_id:
        prev = store.get_source(previous_id)
        assert prev is not None
        version = prev["version"] + 1
    src = store.insert_source(doc_id=doc_id, site_id=site_id, filename=filename,
                              previous_id=previous_id, version=version)
    return str(src["id"])


@pytest.fixture
def no_embed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Podobnosť len fuzzy — testy nesťahujú bge-m3."""
    def broken() -> None:
        raise RuntimeError("bez embedderu")
    monkeypatch.setattr("app.index.embedder.get_embedder", broken)


def test_quote_verification_and_windows() -> None:
    window = make_windows([Segment("Murovanie priečok na 2. NP do piatku.", 3)])[0]
    seg = verify_quote("murovanie priecok na 2. NP", window)
    assert seg is not None and seg.page == 3
    assert verify_quote("Montáž strešnej krytiny", window) is None  # halucinácia
    assert verify_quote("krátke", window) is None
    big = make_windows([Segment("riadok\n" * 1500, 1)], limit=1000)
    assert len(big) > 5 and all(len(w.text) <= 1100 for w in big)


def test_assignment_rules() -> None:
    workers = [
        {"id": "p", "name": "Peter Kováč", "professions_json": '["elektrikár"]', "active": 1},
        {"id": "j", "name": "Ján Malý", "professions_json": '["murár"]', "active": 1},
        {"id": "f", "name": "František Hudec", "professions_json": '["murár"]', "active": 1},
    ]
    assert match_person("Kováč", workers)["id"] == "p"  # type: ignore[index]
    assert match_person("p. Kovac", workers)["id"] == "p"  # type: ignore[index]
    assert match_person("Novák", workers) is None
    busy = [{"id": "x", "site_id": "s2", "status": "todo", "assignees": ["j"],
             "date_from": "2026-10-10", "date_to": "2026-10-20"}]
    s = suggest_worker(person=None, work_type="murár", site_id="s1",
                       date_from=date(2026, 10, 12), date_to=date(2026, 10, 14),
                       workers=workers, tasks=busy)
    assert s.user_id == "f" and "0 úloh v termíne" in s.reason  # Ján je vyťažený
    on_site = [{"id": "y", "site_id": "s1", "status": "in_progress", "assignees": ["j"],
                "date_from": "2026-09-01", "date_to": "2026-09-05"}]
    s2 = suggest_worker(person=None, work_type="murár", site_id="s1",
                        date_from=date(2026, 10, 12), date_to=date(2026, 10, 14),
                        workers=workers, tasks=on_site)
    assert s2.user_id == "j" and "už na stavbe" in s2.reason
    none = suggest_worker(person=None, work_type="pokrývač", site_id="s9", date_from=None,
                          date_to=None, workers=workers, tasks=[])
    assert none.user_id is None and none.basis == "none"


def test_import_pipeline(client: TestClient, fake_llm: FakeLLM, no_embed: None,
                         monkeypatch: pytest.MonkeyPatch) -> None:
    site = make_site(client, "RD Rovinka")
    peter = make_worker(client, "Peter Kováč", professions=["elektrikár"])
    jan = make_worker(client, "Ján Malý", professions=["murár", "betonár"])
    src = _seed_source(client, site, monkeypatch)
    fake_llm.push("_XDocument", {"document_date": "2026-10-05", "tasks": [
        _task("Betonáž základovej dosky", "Do 20. 10. 2026 treba dokončiť betonáž základovej dosky",
              work_type="betonár", date_text="Do 20. 10. 2026"),
        _task("Murovanie obvodových stien", "Po dokončení základov murovanie obvodových stien",
              work_type="murár", date_text="do konca mesiaca", after="Po dokončení základov"),
        _task("Elektroinštalácia 1. NP",
              "Elektroinštaláciu v 1. NP urobí Peter Kováč 2.–6. 11. 2026",
              work_type="elektrikár", person="Peter Kováč", date_text="2.–6. 11. 2026"),
        _task("Montáž strechy", "Montáž strechy do 30.11.", work_type="pokrývač"),  # nie je v texte
        _task("Betonáž základovej dosky", "treba dokončiť betonáž základovej dosky",
              work_type="betonár"),  # duplicita
    ]})
    run_import(src)

    detail = client.get(f"/api/task-imports/{src}").json()
    assert detail["source"]["status"] == "review"
    assert detail["source"]["doc_date"] == "2026-10-05"
    drafts = {d["data"]["title"]: d for d in detail["drafts"]}
    assert set(drafts) == {"Betonáž základovej dosky", "Murovanie obvodových stien",
                           "Elektroinštalácia 1. NP"}
    beton = drafts["Betonáž základovej dosky"]["data"]
    assert beton["date_to"] == "2026-10-20" and beton["assignees"] == [jan]
    assert beton["page"] == 1 and beton["confidence"] in ("high", "medium")
    mur = drafts["Murovanie obvodových stien"]["data"]
    assert mur["date_to"] == "2026-10-31"
    assert mur["depends_on"] == {"draft": 0, "title": "Betonáž základovej dosky"}
    elec = drafts["Elektroinštalácia 1. NP"]["data"]
    assert (elec["date_from"], elec["date_to"]) == ("2026-11-02", "2026-11-06")
    assert elec["assignees"] == [peter] and elec["assign_basis"] == "named"
    assert all(d["kind"] == "new" and d["decision"] == "pending" for d in detail["drafts"])

    # nerozhodnuté → apply odmietne
    resp = client.post(f"/api/task-imports/{src}/apply")
    assert resp.status_code == 409
    client.post(f"/api/task-imports/{src}/decide-all", json={"decision": "approved"})
    counts = client.post(f"/api/task-imports/{src}/apply").json()
    assert counts == {"created": 3, "updated": 0, "cancelled": 0}

    tasks = {t["title"]: t for t in store.list_tasks(site_id=site)}
    assert tasks["Murovanie obvodových stien"]["depends_on"] == \
        tasks["Betonáž základovej dosky"]["id"]
    assert tasks["Elektroinštalácia 1. NP"]["source_quote"].startswith("Elektroinštaláciu")
    assert client.get(f"/api/task-imports/{src}").json()["source"]["status"] == "applied"


def test_draft_without_term_blocks_apply(client: TestClient, fake_llm: FakeLLM,
                                         no_embed: None, monkeypatch: pytest.MonkeyPatch) -> None:
    site = make_site(client)
    src = _seed_source(client, site, monkeypatch, text="Treba urobiť omietky v garáži čo najskôr.")
    fake_llm.push("_XDocument", {"tasks": [
        _task("Omietky v garáži", "Treba urobiť omietky v garáži", date_text="čo najskôr"),
    ]})
    run_import(src)
    draft = client.get(f"/api/task-imports/{src}").json()["drafts"][0]
    assert draft["data"]["confidence"] == "low"
    assert "chýba termín" in draft["data"]["issues"]
    assert "nikto nenavrhnutý" in draft["data"]["issues"]
    # „schváliť všetko“ preskočí návrh bez termínu
    client.post(f"/api/task-imports/{src}/decide-all", json={"decision": "approved"})
    assert client.get(f"/api/task-imports/{src}").json()["drafts"][0]["decision"] == "pending"
    # majiteľ doplní termín a schváli
    resp = client.put(f"/api/task-imports/{src}/drafts/{draft['id']}", json={
        "title": "Omietky v garáži", "date_from": "2026-10-12", "date_to": "2026-10-14",
        "assignees": [], "decision": "approved",
    })
    assert resp.status_code == 200 and resp.json()["data"]["issues"] == ["nikto nenavrhnutý"]
    assert client.post(f"/api/task-imports/{src}/apply").json()["created"] == 1


def test_new_version_diff(client: TestClient, fake_llm: FakeLLM, no_embed: None,
                          monkeypatch: pytest.MonkeyPatch) -> None:
    site = make_site(client)
    jan = make_worker(client, "Ján", professions=["murár"])
    v1_text = ("Murovanie priečok 2. NP do 20.10.2026. Omietky 1. NP do 25.10.2026. "
               "Lešenie do 15.10.2026.")
    v1 = _seed_source(client, site, monkeypatch, text=v1_text, filename="harmonogram-v1.pdf")
    fake_llm.push("_XDocument", {"tasks": [
        _task("Murovanie priečok 2. NP", "Murovanie priečok 2. NP do 20.10.2026",
              work_type="murár", date_text="do 20.10.2026"),
        _task("Omietky 1. NP", "Omietky 1. NP do 25.10.2026", date_text="do 25.10.2026"),
        _task("Postavenie lešenia", "Lešenie do 15.10.2026", date_text="do 15.10.2026"),
    ]})
    run_import(v1)
    client.post(f"/api/task-imports/{v1}/decide-all", json={"decision": "approved"})
    # omietky nemajú pracovníka, ale termín áno → schválené; apply
    assert client.post(f"/api/task-imports/{v1}/apply").status_code == 200
    by_title = {t["title"]: t for t in store.list_tasks(site_id=site)}
    store.update_task(by_title["Postavenie lešenia"]["id"], {"status": "in_progress"})

    suggestion = client.get("/api/task-imports/suggest-previous",
                            params={"site_id": site, "filename": "harmonogram-v2.pdf"}).json()
    assert suggestion["id"] == v1

    v2_text = ("Murovanie priečok 2. NP do 27.10.2026. Omietky 1. NP do 25.10.2026. "
               "Obklady kúpeľne do 10.11.2026.")
    v2 = _seed_source(client, site, monkeypatch, text=v2_text, previous_id=v1,
                      filename="harmonogram-v2.pdf")
    fake_llm.push("_XDocument", {"tasks": [
        _task("Murovanie priečok 2. NP", "Murovanie priečok 2. NP do 27.10.2026",
              work_type="murár", date_text="do 27.10.2026"),
        _task("Omietky 1. NP", "Omietky 1. NP do 25.10.2026", date_text="do 25.10.2026"),
        _task("Obklady kúpeľne", "Obklady kúpeľne do 10.11.2026", date_text="do 10.11.2026"),
    ]})
    run_import(v2)
    drafts = client.get(f"/api/task-imports/{v2}").json()["drafts"]
    kinds = {d["data"]["title"]: d for d in drafts}
    assert kinds["Murovanie priečok 2. NP"]["kind"] == "changed"
    assert kinds["Murovanie priečok 2. NP"]["data"]["changes"]["date_to"] == \
        ["2026-10-20", "2026-10-27"]
    assert kinds["Murovanie priečok 2. NP"]["data"]["assignees"] == [jan]  # ponechaný
    assert kinds["Omietky 1. NP"]["kind"] == "unchanged"
    assert kinds["Omietky 1. NP"]["decision"] == "approved"
    assert kinds["Obklady kúpeľne"]["kind"] == "new"
    cancelled = kinds["Postavenie lešenia"]
    assert cancelled["kind"] == "cancelled"
    assert cancelled["data"]["issues"] == ["úloha je rozpracovaná"]

    for d in drafts:
        if d["decision"] == "pending":
            client.put(f"/api/task-imports/{v2}/drafts/{d['id']}", json={
                "title": d["data"]["title"], "description": d["data"].get("description") or "",
                "work_type": d["data"].get("work_type"), "date_from": d["data"]["date_from"],
                "date_to": d["data"]["date_to"], "assignees": d["data"]["assignees"],
                "decision": "approved",
            })
    counts = client.post(f"/api/task-imports/{v2}/apply").json()
    assert counts == {"created": 1, "updated": 1, "cancelled": 1}
    after = {t["title"]: t for t in store.list_tasks(site_id=site)}
    assert after["Murovanie priečok 2. NP"]["date_to"] == "2026-10-27"
    assert after["Murovanie priečok 2. NP"]["source_id"] == v2
    assert after["Postavenie lešenia"]["status"] == "cancelled"
    assert after["Omietky 1. NP"]["source_id"] == v2


def test_upload_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    jobs: list[str] = []
    monkeypatch.setattr("app.api.task_import.schedule_ingest", lambda doc_id: None)
    monkeypatch.setattr("app.api.task_import.schedule", lambda fn, sid: jobs.append(sid))
    site = make_site(client)
    resp = client.post("/api/task-imports", data={"site_id": site},
                       files={"file": ("zadanie.md", b"# Zadanie\n\nMurovanie do piatku.",
                                       "text/markdown")})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "waiting" and body["version"] == 1 and jobs == [body["id"]]
    bad = client.post("/api/task-imports", data={"site_id": site},
                      files={"file": ("x.exe", b"MZ\x00\x01", "application/octet-stream")})
    assert bad.status_code == 415
    no_site = client.post("/api/task-imports", data={"site_id": "nie"},
                          files={"file": ("a.md", b"# a", "text/markdown")})
    assert no_site.status_code == 422
    v2 = client.post("/api/task-imports", data={"site_id": site, "previous_id": body["id"]},
                     files={"file": ("zadanie2.md", b"# Zadanie v2\n\nMurovanie.",
                                     "text/markdown")})
    assert v2.json()["version"] == 2


def test_import_error_on_failed_document(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    site = make_site(client)
    doc_id = store.new_id()
    db.insert_document(doc_id, "zly.pdf", "sha-zly")
    db.set_document_status(doc_id, "error", error="poškodené PDF")
    src = store.insert_source(doc_id=doc_id, site_id=site, filename="zly.pdf", previous_id=None,
                              version=1)
    run_import(src["id"])
    row = store.get_source(src["id"])
    assert row is not None and row["status"] == "error" and "poškodené" in row["error"]


def test_term_prefix_and_typo_tolerant_diff(no_embed: None) -> None:
    from app.workforce.importer import Draft, diff_with_previous, expand_term

    assert expand_term("23. 10. 2026", "betonáž sa posúva do 23. 10. 2026") == "do 23. 10. 2026"
    assert expand_term("2.–6. 11.", "urobí Peter v termíne 2.–6. 11.") == "2.–6. 11."
    old = [{"id": "t1", "title": "Betonáž základovej dosky", "description": "pôvodný popis",
            "work_type": "betonár", "date_from": "2026-10-08", "date_to": "2026-10-20",
            "status": "todo", "assignees": ["j"]}]
    new = Draft({"title": "Betónáž základovej dosky", "description": "iný popis od LLM",
                 "work_type": "betonár", "date_from": "2026-10-12", "date_to": "2026-10-20",
                 "date_from_derived": True, "assignees": [], "quote": "q", "page": 1})
    out = diff_with_previous([new], old)
    assert out[0].kind == "unchanged"  # preklep, iný popis ani dopočítaný začiatok nie sú zmena
    assert out[0].data["title"] == "Betonáž základovej dosky"
    assert out[0].data["description"] == "pôvodný popis"


def test_duplicate_of_existing_task_is_flagged(
    client: TestClient, fake_llm: FakeLLM, no_embed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.wf_fixtures import make_task

    site = make_site(client)
    make_task(client, site, "Doplnenie hydroizolácie pri výťahovej šachte", [])
    src = _seed_source(client, site, monkeypatch,
                       text="Hydroizoláciu pri výťahovej šachte treba doplniť do 9. 10. 2026.")
    fake_llm.push("_XDocument", {"tasks": [
        _task("Doplnenie hydroizolácie pri výťahovej šachte",
              "Hydroizoláciu pri výťahovej šachte treba doplniť do 9. 10. 2026",
              date_text="do 9. 10. 2026"),
    ]})
    run_import(src)
    draft = client.get(f"/api/task-imports/{src}").json()["drafts"][0]
    assert draft["data"]["duplicate_of"]["title"].startswith("Doplnenie hydroizolácie")
    assert any("podobná úloha" in i for i in draft["data"]["issues"])
    client.post(f"/api/task-imports/{src}/decide-all", json={"decision": "approved"})
    assert client.get(f"/api/task-imports/{src}").json()["drafts"][0]["decision"] == "pending"

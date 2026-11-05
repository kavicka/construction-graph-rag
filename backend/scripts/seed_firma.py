"""Fill a running backend with the company Stavby Kráľ: workers, sites and tasks imported from
documents via the real AI import (the same path as „Nahrať dokument“ in the UI).

    uv run python scripts/seed_firma.py                       # http://localhost:8000, data/
    uv run python scripts/seed_firma.py --api http://… --data-dir /path --email … --password …

Admin credentials: --email/--password, else RAG_ADMIN_EMAIL / RAG_ADMIN_PASSWORD from the
environment, else from backend/.env. Worker PINs are generated randomly and saved to
<data-dir>/workforce/piny-pracovnikov.txt (for you only). Documents from the minutes are saved to
<data-dir>/seed-docs/. Task proposals are left for approval — it prints them.

The minutes are dated --base-date (default 2026-10-06); every deadline in them is relative to it,
so a fresh base date gives tasks that are active today. The demo (backend/scripts/demo_seed.py)
reuses these functions.
"""

import argparse
import os
import secrets
import sys
import time
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

WORKERS = [
    ("Marek Hudák", ["murár"]),
    ("Tomáš Baláž", ["betonár", "železiar"]),
    ("Jozef Mikuš", ["tesár"]),
    ("Michal Ondruš", ["izolatér"]),
    ("Rastislav Ďurica", ["strojník", "pomocný robotník"]),
    ("Lukáš Sloboda", ["elektrikár"]),
    ("Martin Krajči", ["sadrokartonista", "maliar"]),
]
SITES = [
    ("Bytový dom Agátová", "Agátová 22, Bratislava-Dúbravka", True),
    ("Logistická hala Senec", "Senec Logistics Park", True),
    ("Rekonštrukcia ZŠ Hrnčiarska", "Hrnčiarska, Žilina", False),
]
DEFAULT_BASE_DATE = date(2026, 10, 6)
# already in the library when the construction corpus is ingested (eval/construction.py)
PROTOKOL = "protokol-kontroly-kvality-agatova.pdf"
MONTHS_SK = [
    "január",
    "február",
    "marec",
    "apríl",
    "máj",
    "jún",
    "júl",
    "august",
    "september",
    "október",
    "november",
    "december",
]

CSS = """<style>@page{size:A4;margin:20mm} body{font-family:Arial,sans-serif;font-size:10.5pt;
line-height:1.5;color:#111} h1{font-size:15pt;margin:0 0 4mm} .meta{color:#555;font-size:9pt}
table{border-collapse:collapse;width:100%;margin:3mm 0} td,th{border:1px solid #999;
padding:4px 6px;font-size:9.5pt;text-align:left} th{background:#eee}</style>"""


def _sk(d: date) -> str:
    return f"{d.day}. {d.month}. {d.year}"


def _sk_range(a: date, b: date) -> str:
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}.–{_sk(b)}"
    return f"{_sk(a)} – {_sk(b)}"


def zapis_agatova(base: date) -> str:
    def d(days: int) -> str:
        return _sk(base + timedelta(days=days))

    return (
        CSS
        + f"""
<h1>Zápis z kontrolného dňa č. 14 — Bytový dom Agátová</h1>
<p class="meta">Dátum: {d(0)} · Miesto: stavenisko Agátová 22 · Prítomní: Ing. Lucia
Šimková (technický dozor), Ing. Peter Hronec (stavbyvedúci, Stavby Kráľ), Ing. Juraj Bielik
(statik), zástupca Elektro Lux s.r.o.</p>
<p><b>1. Stav prác.</b> Hrubá stavba je dokončená po strop nad 3. NP. Vada D-01 (trhlina v stene
1. NP) bola odstránená injektážou {d(-27)} a je uzavretá. Jadrový vývrt zo stropu nad 2. NP
(vzorka K-03) vyhovel, strop je možné zaťažiť.</p>
<p><b>2. Úlohy zhotoviteľa.</b> Betonáž stropu nad 4. NP treba urobiť do {d(10)}, betón
C30/37 dodá Betón Senec; pred betonážou prevezme výstuž statik Ing. Bielik, výstuž stropu
pripraví Tomáš Baláž do {d(7)}. Po betonáži stropu nad 4. NP pokračuje murovanie
obvodových stien 5. NP z tehál Porotherm 44 Profi, termín do konca mesiaca. Vada D-02 —
chýbajúca hydroizolácia pri výťahovej šachte — stále nie je doplnená, treba ju doplniť do
piatku. Lešenie na severnej fasáde treba nadstaviť o dve podlažia budúci týždeň, urobí to
Rastislav Ďurica. Debnenie schodiska medzi 3. a 4. NP pripraviť do {d(6)}.</p>
<p><b>3. Subdodávatelia.</b> Elektro Lux pokračuje v rozvodoch v 1. a 2. NP; zhotoviteľ zabezpečí
prístup a uzamykateľný sklad káblov do {d(3)}.</p>
<p><b>4. Rôzne.</b> Faktúru za september uhradí objednávateľ do {d(14)}. Ďalší kontrolný
deň bude {d(14)} o 9:00.</p>
<p>Zapísal: Ing. Peter Hronec</p>
"""
    )


def harmonogram_senec(base: date) -> str:
    def at(days: int) -> date:
        return base + timedelta(days=days)

    first, last = at(0), at(30)
    period = (
        f"{MONTHS_SK[first.month - 1]} – {MONTHS_SK[last.month - 1]} {last.year}"
        if first.year == last.year
        else f"{MONTHS_SK[first.month - 1]} {first.year} – {MONTHS_SK[last.month - 1]} {last.year}"
    )
    week = at(22).isocalendar().week
    return (
        CSS
        + f"""
<h1>Harmonogram prác — Logistická hala Senec ({period})</h1>
<p class="meta">Vypracovala: Ing. Mária Kováčová, stavbyvedúca · Aktualizácia {_sk(at(-1))}</p>
<table>
<tr><th>Činnosť</th><th>Termín</th><th>Kto</th><th>Poznámka</th></tr>
<tr><td>Montáž oceľovej konštrukcie haly, osi A–F</td><td>{_sk_range(at(-1), at(10))}</td>
<td>Oceľ Krupina s.r.o.</td><td>subdodávka</td></tr>
<tr><td>Debnenie a betonáž nakladacích rámp 1–3</td><td>{_sk_range(at(6), at(15))}</td>
<td>vlastné kapacity</td><td>betón C25/30</td></tr>
<tr><td>Prípojka NN pre staveniskový rozvádzač</td><td>do {_sk(at(8))}</td>
<td>elektrikár</td><td></td></tr>
<tr><td>Betonáž priemyselnej podlahy haly, úsek 1</td><td>{_sk_range(at(13), at(17))}</td>
<td>vlastné kapacity</td><td>vláknobetón</td></tr>
<tr><td>Opláštenie haly sendvičovými panelmi</td><td>po dokončení oceľovej konštrukcie</td>
<td>Strechy Tatra s.r.o.</td><td>subdodávka</td></tr>
<tr><td>Sadrokartónové priečky v administratívnej vstavbe</td><td>v {week}. týždni</td>
<td>vlastné kapacity</td><td></td></tr>
</table>
<p>Upozornenie: v areáli platí obmedzenie hlučných prác po 18:00.</p>
"""
    )


def env_value(key: str) -> str:
    """RAG_* value from the environment, else from backend/.env."""
    if value := os.environ.get(key):
        return value
    env_file = ROOT / "backend" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} is missing — pass --email/--password or set it in the environment")


def make_pdf(html: str, path: Path) -> None:
    from app.docgen.pdf import render_pdf

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_pdf(html, allowed_dirs=[]).data)


def login(api: str, email: str, password: str) -> httpx.Client:
    client = httpx.Client(base_url=f"{api}/api", timeout=120)
    resp = client.post("/auth/login", json={"email": email, "password": password})
    if resp.status_code != 200:
        raise SystemExit(f"login as {email} failed: {resp.status_code} {resp.text}")
    return client


def wait_review(client: httpx.Client, source_id: str) -> dict[str, Any]:
    t0 = time.time()
    while True:
        d = client.get(f"/task-imports/{source_id}").json()
        if d["source"]["status"] in ("review", "error", "applied"):
            print(f"  → {d['source']['status']} in {time.time() - t0:.0f} s")
            return dict(d)
        time.sleep(2)


def seed_workers(
    client: httpx.Client, pins: Mapping[str, str] | None = None
) -> list[tuple[str, str]]:
    """Creates the missing workers; PIN from `pins` or random. Returns (name, PIN) of new ones."""
    existing = {w["name"] for w in client.get("/workers").json()}
    created: list[tuple[str, str]] = []
    for name, professions in WORKERS:
        if name in existing:
            continue
        pin = (pins or {}).get(name) or f"{secrets.randbelow(10**4):04d}"
        client.post(
            "/workers", json={"name": name, "pin": pin, "professions": professions}
        ).raise_for_status()
        created.append((name, pin))
    return created


def seed_sites(client: httpx.Client) -> dict[str, dict[str, Any]]:
    sites = {s["name"]: s for s in client.get("/sites").json()}
    for name, address, active in SITES:
        if name not in sites:
            resp = client.post("/sites", json={"name": name, "address": address, "active": active})
            resp.raise_for_status()
            sites[name] = resp.json()
    return sites


def seed_imports(
    client: httpx.Client,
    sites: Mapping[str, Mapping[str, Any]],
    docs_dir: Path,
    base: date = DEFAULT_BASE_DATE,
    after_review: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Runs the AI task import over the minutes (+ the QA protocol if it is in the library).
    Skips files already imported. Returns the details of the imports started now; `after_review`
    gets each one before the next import starts (e.g. to approve it, so the next import sees its
    tasks and can mark duplicates)."""
    zapis = docs_dir / f"zapis-kd14-agatova-{base.isoformat()}.pdf"
    harmonogram = docs_dir / f"harmonogram-senec-{base:%Y-%m}.pdf"
    make_pdf(zapis_agatova(base), zapis)
    make_pdf(harmonogram_senec(base), harmonogram)

    imports: list[tuple[str, bytes, str]] = [
        (zapis.name, zapis.read_bytes(), "Bytový dom Agátová"),
        (harmonogram.name, harmonogram.read_bytes(), "Logistická hala Senec"),
    ]
    library = {d["filename"]: d for d in client.get("/documents").json()}
    if protokol := library.get(PROTOKOL):
        pdf = client.get(f"/documents/{protokol['id']}/pdf")
        pdf.raise_for_status()
        imports.insert(0, (PROTOKOL, pdf.content, "Bytový dom Agátová"))

    done = {s["filename"] for s in client.get("/task-imports").json()}
    details: list[dict[str, Any]] = []
    for filename, content, site in imports:
        if filename in done:
            continue
        print(f"import {filename} → {site}")
        src = client.post(
            "/task-imports",
            data={"site_id": sites[site]["id"]},
            files={"file": (filename, content, "application/pdf")},
        )
        src.raise_for_status()
        detail = wait_review(client, src.json()["id"])
        if after_review is not None:
            after_review(detail)
        details.append(detail)
    return details


def print_drafts(client: httpx.Client, detail: Mapping[str, Any]) -> None:
    workers = {w["id"]: w["name"] for w in client.get("/workers").json()}
    for d in detail["drafts"]:
        x = d["data"]
        who = ", ".join(workers.get(a, "?") for a in x.get("assignees", [])) or "—"
        print(
            f"  [{x.get('confidence')}] {x['title']} | {x.get('date_from')}–"
            f"{x.get('date_to')} | {who} | {'; '.join(x.get('issues', []))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--email", help="admin e-mail (default: RAG_ADMIN_EMAIL)")
    parser.add_argument("--password", help="admin password (default: RAG_ADMIN_PASSWORD)")
    parser.add_argument(
        "--base-date", type=date.fromisoformat, default=DEFAULT_BASE_DATE, help="YYYY-MM-DD"
    )
    args = parser.parse_args()
    client = login(
        args.api,
        args.email or env_value("RAG_ADMIN_EMAIL"),
        args.password or env_value("RAG_ADMIN_PASSWORD"),
    )

    created = seed_workers(client)
    if created:
        pin_file = args.data_dir / "workforce" / "piny-pracovnikov.txt"
        pin_file.parent.mkdir(parents=True, exist_ok=True)
        with pin_file.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(f"{name}: {pin}" for name, pin in created) + "\n")
        pin_file.chmod(0o600)
        print(f"workers: {len(created)} new, PINs in {pin_file}")

    sites = seed_sites(client)
    print("sites:", ", ".join(sites))
    for detail in seed_imports(client, sites, args.data_dir / "seed-docs", args.base_date):
        print_drafts(client, detail)


if __name__ == "__main__":
    main()

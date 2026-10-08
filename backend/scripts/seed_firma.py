"""Naplní bežiaci backend firmou Stavby Kráľ: pracovníci, stavby a úlohy importované z
dokumentov skutočným AI importom (rovnaká cesta ako „Nahrať dokument“ v UI).

    uv run python scripts/seed_firma.py                 # http://localhost:8000
    uv run python scripts/seed_firma.py --api http://…  # iný backend

Majiteľa prihlási údajmi z backend/.env (RAG_ADMIN_EMAIL / RAG_ADMIN_PASSWORD). PIN-y
pracovníkov vygeneruje náhodne a uloží do data/workforce/piny-pracovnikov.txt (len pre teba).
Dokumenty zo zápisov uloží do data/seed-docs/. Návrhy úloh nechá na schválenie — vypíše ich.
"""

import argparse
import secrets
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
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

CSS = """<style>@page{size:A4;margin:20mm} body{font-family:Arial,sans-serif;font-size:10.5pt;
line-height:1.5;color:#111} h1{font-size:15pt;margin:0 0 4mm} .meta{color:#555;font-size:9pt}
table{border-collapse:collapse;width:100%;margin:3mm 0} td,th{border:1px solid #999;
padding:4px 6px;font-size:9.5pt;text-align:left} th{background:#eee}</style>"""

ZAPIS_AGATOVA = CSS + """
<h1>Zápis z kontrolného dňa č. 14 — Bytový dom Agátová</h1>
<p class="meta">Dátum: 6. 10. 2026 · Miesto: stavenisko Agátová 22 · Prítomní: Ing. Lucia
Šimková (technický dozor), Ing. Peter Hronec (stavbyvedúci, Stavby Kráľ), Ing. Juraj Bielik
(statik), zástupca Elektro Lux s.r.o.</p>
<p><b>1. Stav prác.</b> Hrubá stavba je dokončená po strop nad 3. NP. Vada D-01 (trhlina v stene
1. NP) bola odstránená injektážou 9. 9. 2026 a je uzavretá. Jadrový vývrt zo stropu nad 2. NP
(vzorka K-03) vyhovel, strop je možné zaťažiť.</p>
<p><b>2. Úlohy zhotoviteľa.</b> Betonáž stropu nad 4. NP treba urobiť do 16. 10. 2026, betón
C30/37 dodá Betón Senec; pred betonážou prevezme výstuž statik Ing. Bielik, výstuž stropu
pripraví Tomáš Baláž do 13. 10. 2026. Po betonáži stropu nad 4. NP pokračuje murovanie
obvodových stien 5. NP z tehál Porotherm 44 Profi, termín do konca mesiaca. Vada D-02 —
chýbajúca hydroizolácia pri výťahovej šachte — stále nie je doplnená, treba ju doplniť do
piatku. Lešenie na severnej fasáde treba nadstaviť o dve podlažia budúci týždeň, urobí to
Rastislav Ďurica. Debnenie schodiska medzi 3. a 4. NP pripraviť do 12. 10. 2026.</p>
<p><b>3. Subdodávatelia.</b> Elektro Lux pokračuje v rozvodoch v 1. a 2. NP; zhotoviteľ zabezpečí
prístup a uzamykateľný sklad káblov do 9. 10. 2026.</p>
<p><b>4. Rôzne.</b> Faktúru za september uhradí objednávateľ do 20. 10. 2026. Ďalší kontrolný
deň bude 20. 10. 2026 o 9:00.</p>
<p>Zapísal: Ing. Peter Hronec</p>
"""

HARMONOGRAM_SENEC = CSS + """
<h1>Harmonogram prác — Logistická hala Senec (október – november 2026)</h1>
<p class="meta">Vypracovala: Ing. Mária Kováčová, stavbyvedúca · Aktualizácia 5. 10. 2026</p>
<table>
<tr><th>Činnosť</th><th>Termín</th><th>Kto</th><th>Poznámka</th></tr>
<tr><td>Montáž oceľovej konštrukcie haly, osi A–F</td><td>5.–16. 10. 2026</td>
<td>Oceľ Krupina s.r.o.</td><td>subdodávka</td></tr>
<tr><td>Debnenie a betonáž nakladacích rámp 1–3</td><td>12.–21. 10. 2026</td>
<td>vlastné kapacity</td><td>betón C25/30</td></tr>
<tr><td>Prípojka NN pre staveniskový rozvádzač</td><td>do 14. 10. 2026</td>
<td>elektrikár</td><td></td></tr>
<tr><td>Betonáž priemyselnej podlahy haly, úsek 1</td><td>19.–23. 10. 2026</td>
<td>vlastné kapacity</td><td>vláknobetón</td></tr>
<tr><td>Opláštenie haly sendvičovými panelmi</td><td>po dokončení oceľovej konštrukcie</td>
<td>Strechy Tatra s.r.o.</td><td>subdodávka</td></tr>
<tr><td>Sadrokartónové priečky v administratívnej vstavbe</td><td>v 44. týždni</td>
<td>vlastné kapacity</td><td></td></tr>
</table>
<p>Upozornenie: v areáli platí obmedzenie hlučných prác po 18:00.</p>
"""


def env_value(key: str) -> str:
    for line in (ROOT / "backend" / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} chýba v backend/.env")


def make_pdf(html: str, path: Path) -> None:
    from app.docgen.pdf import render_pdf

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_pdf(html, allowed_dirs=[]).data)


def wait_review(client: httpx.Client, source_id: str) -> dict:  # type: ignore[type-arg]
    t0 = time.time()
    while True:
        d = client.get(f"/task-imports/{source_id}").json()
        if d["source"]["status"] in ("review", "error", "applied"):
            print(f"  → {d['source']['status']} za {time.time() - t0:.0f} s")
            return dict(d)
        time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()
    client = httpx.Client(base_url=f"{args.api}/api", timeout=120)
    resp = client.post("/auth/login", json={"email": env_value("RAG_ADMIN_EMAIL"),
                                            "password": env_value("RAG_ADMIN_PASSWORD")})
    resp.raise_for_status()

    existing = {w["name"]: w for w in client.get("/workers").json()}
    pins: list[str] = []
    for name, professions in WORKERS:
        if name in existing:
            continue
        pin = f"{secrets.randbelow(10**4):04d}"
        client.post("/workers", json={"name": name, "pin": pin, "professions": professions}
                    ).raise_for_status()
        pins.append(f"{name}: {pin}")
    if pins:
        pin_file = DATA / "workforce" / "piny-pracovnikov.txt"
        pin_file.parent.mkdir(parents=True, exist_ok=True)
        with pin_file.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(pins) + "\n")
        pin_file.chmod(0o600)
        print(f"pracovníci: {len(pins)} nových, PIN-y v {pin_file}")

    sites = {s["name"]: s for s in client.get("/sites").json()}
    for name, address, active in SITES:
        if name not in sites:
            sites[name] = client.post("/sites", json={"name": name, "address": address,
                                                      "active": active}).json()
    print("stavby:", ", ".join(sites))

    docs_dir = DATA / "seed-docs"
    make_pdf(ZAPIS_AGATOVA, docs_dir / "zapis-kd14-agatova-2026-10-06.pdf")
    make_pdf(HARMONOGRAM_SENEC, docs_dir / "harmonogram-senec-2026-10.pdf")
    library = {d["filename"]: d for d in client.get("/documents").json()}
    protokol = library.get("protokol-kontroly-kvality-agatova.pdf")

    imports = [
        (docs_dir / "zapis-kd14-agatova-2026-10-06.pdf", "Bytový dom Agátová"),
        (docs_dir / "harmonogram-senec-2026-10.pdf", "Logistická hala Senec"),
    ]
    if protokol:
        imports.insert(0, (DATA / "pdfs" / f"{protokol['id']}.pdf", "Bytový dom Agátová"))
    done = {s["filename"] for s in client.get("/task-imports").json()}
    for path, site in imports:
        filename = "protokol-kontroly-kvality-agatova.pdf" if path.parent.name == "pdfs" \
            else path.name
        if filename in done:
            continue
        print(f"import {filename} → {site}")
        src = client.post("/task-imports", data={"site_id": sites[site]["id"]},
                          files={"file": (filename, path.read_bytes(), "application/pdf")})
        src.raise_for_status()
        detail = wait_review(client, src.json()["id"])
        workers = {w["id"]: w["name"] for w in client.get("/workers").json()}
        for d in detail["drafts"]:
            x = d["data"]
            who = ", ".join(workers.get(a, "?") for a in x.get("assignees", [])) or "—"
            print(f"  [{x.get('confidence')}] {x['title']} | {x.get('date_from')}–"
                  f"{x.get('date_to')} | {who} | {'; '.join(x.get('issues', []))}")


if __name__ == "__main__":
    main()

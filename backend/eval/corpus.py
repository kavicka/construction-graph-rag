"""Deterministický vzorkový korpus pre eval — 3 PDF s prekrývajúcimi sa konceptmi.

meranie-sk.pdf     SK meranie prietoku + tabuľka hodnôt
calibration-en.pdf EN kalibrácia snímača prietoku + tabuľka tolerancií (cross-doc koncept)
skladovanie-sk.pdf SK šum — nesúvisiaca téma (test presnosti)
"""

from pathlib import Path

from eval.pdfgen import Block, Page, render_pages


def _doc(path: Path, title: str, paragraphs: list[str], table_data: list[list[str]] | None) -> None:
    page: list[Block] = [("p", p) for p in paragraphs]
    if table_data:
        page.append(("table", table_data))
    render_pages(path, title, [page])


def generate_corpus(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    p1 = out_dir / "meranie-sk.pdf"
    _doc(
        p1,
        "Meranie prietoku chladiaceho média",
        [
            "Tento protokol zhŕňa meranie prietoku chladiaceho média v testovacom okruhu. "
            "Prietok bol meraný snímačom FL-200 pri atmosférickom tlaku 101.3 kPa.",
            "Merania prebehli na vzorkách A-01 až B-02. Snímač prietoku FL-200 vyžaduje "
            "pravidelnú kalibráciu podľa kalibračného protokolu.",
        ],
        [
            ["Vzorka", "Prietok (l/min)", "Teplota (°C)"],
            ["A-01", "12.4", "21.5"],
            ["A-02", "13.1", "22.0"],
            ["B-01", "9.8", "20.9"],
            ["B-02", "10.2", "21.2"],
        ],
    )
    p2 = out_dir / "calibration-en.pdf"
    _doc(
        p2,
        "Coolant flow sensor calibration procedure",
        [
            "The FL-200 coolant flow sensor must be calibrated every 30 days. "
            "Calibration ensures accurate flow measurement across the operating range.",
            "The maximum permissible deviation after calibration is listed below. "
            "Sensors exceeding the deviation limit must be replaced.",
        ],
        [
            ["Sensor", "Range (l/min)", "Max deviation (%)"],
            ["FL-200", "5-50", "0.5"],
            ["FL-350", "20-120", "0.8"],
        ],
    )
    p3 = out_dir / "skladovanie-sk.pdf"
    _doc(
        p3,
        "Skladovanie laboratórnych vzoriek",
        [
            "Vzorky sa skladujú pri teplote 4 °C v uzavretých nádobách. "
            "Maximálna doba skladovania je 14 dní od odberu.",
            "Po uplynutí doby skladovania sa vzorky likvidujú podľa smernice o odpadoch.",
        ],
        None,
    )
    p4 = out_dir / "jedalnicek-sk.pdf"
    _meal_plan(p4)
    return [p1, p2, p3, p4]


def _meal_plan(path: Path) -> None:
    """Jedálniček s kategóriami ako nadpismi NAD tabuľkami — testuje caption backfill,
    records extrakciu a filter/compose otázky."""
    header = ["Číslo", "Název jídla", "Ingredience", "Macra"]
    page: Page = [
        ("h", "Obedy"),
        ("table", [
            header,
            ["10", "Stir-fry s hovězím", "hovězí 150g, rýže 100g", "900 kcal 50b, 80s, 40t"],
            ["11", "Kuřecí rizoto", "kuře 150g, rýže 120g", "829 kcal 74b, 84s, 17t"],
            ["12", "Těstoviny s kuřecím", "těstoviny 120g, kuře 150g", "820 kcal 55b, 90s, 20t"],
        ]),
        ("h", "Raňajky"),
        ("table", [
            header,
            ["1", "Ovesná kaše", "vločky 80g, mléko 250ml", "447 kcal 30b, 60s, 12t"],
            ["2", "Toasty se šunkou", "toast 4ks, šunka 80g", "477 kcal 25b, 55s, 18t"],
        ]),
    ]
    render_pages(path, "Jedálniček 2800 kcal", [page])

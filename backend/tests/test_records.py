import json

from app import db
from app.config import get_settings
from app.ingest.records import extract_records, parse_cell_attrs


def test_parse_cell_attrs_meal_macros() -> None:
    attrs = parse_cell_attrs("548 kcal 61b, 49s, 10t")
    assert attrs == {"kcal": 548.0, "b": 61.0, "s": 49.0, "t": 10.0}

    attrs = parse_cell_attrs("829kcal 74b, 84s, 17t")
    assert attrs["kcal"] == 829.0
    assert attrs["b"] == 74.0


def test_parse_cell_attrs_guards_against_false_shorthand() -> None:
    # samotné 'b' v bežnom texte bez kcal a bez trojice sa nesmie chytiť
    assert "b" not in parse_cell_attrs("odstavec b hovorí o miešaní")
    # jednotky fungujú aj bez kcal
    attrs = parse_cell_attrs("toast 2ks 50g, tvaroh 125g")
    assert attrs["val_g"] == 50.0
    assert attrs["val_ks"] == 2.0


def _seed_table(doc_id: str, caption: str | None, md: str) -> str:
    settings = get_settings()
    settings.ensure_dirs()
    db.init_db()
    if db.get_document(doc_id) is None:
        db.insert_document(doc_id, f"{doc_id}.pdf", f"sha-{doc_id}")
    table_id = f"tbl-{doc_id}-{abs(hash(md)) % 10_000}"
    md_path = settings.tables_dir / f"{table_id}.md"
    md_path.write_text(md, encoding="utf-8")
    db.insert_table(table_id, doc_id, 5, caption, 3, 4, str(md_path), None)
    return table_id


MEAL_MD = """| Číslo | Název jídla | Ingredience | Macra |
|---|---|---|---|
| 10 | Stir-fry s hovězím | hovězí 150g, rýže 100g | 829 kcal 74b, 84s, 17t |
| 11 | Těstoviny s kuřecím | těstoviny 120g, kuře 150g | 820 kcal 55b, 90s, 20t |
| 12 | Poznámka bez čísel | — | — |"""


def test_extract_records_from_meal_table() -> None:
    _seed_table("d1", "Oběd varianty", MEAL_MD)
    stored = extract_records("d1")
    assert stored == 2  # riadok bez čísel sa preskočí

    records = db.list_records("d1")
    by_name = {r["name"]: json.loads(r["attrs_json"]) for r in records}
    assert by_name["Stir-fry s hovězím"]["kcal"] == 829.0
    assert by_name["Těstoviny s kuřecím"]["b"] == 55.0
    assert all(r["category"] == "Oběd varianty" for r in records)
    assert all(r["page_no"] == 5 for r in records)


def test_extract_records_idempotent_and_fallback_name() -> None:
    _seed_table("d2", None, "| A | B |\n|---|---|\n| dlhý popisný názov položky | 12.4 kcal |")
    extract_records("d2")
    extract_records("d2")
    records = db.list_records("d2")
    assert len(records) == 1
    assert records[0]["name"] == "dlhý popisný názov položky"
    assert records[0]["category"] is None

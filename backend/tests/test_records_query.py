import json

import pytest

from app import db
from app.config import get_settings
from app.retrieve.records_query import build_filter_context, run_filter
from app.retrieve.router import Constraint, QueryPlan
from app.retrieve.solver import Slot, pick_items


@pytest.fixture
def meals() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "jedla.pdf", "sha-1")
    db.insert_table("t-obed", "d1", 14, "Oběd varianty", 3, 4, "/tmp/x.md", None)
    db.insert_table("t-snidane", "d1", 5, "Snídaně", 2, 4, "/tmp/y.md", None)
    data = [
        ("t-obed", "Oběd varianty", "Stir-fry", {"kcal": 900, "b": 50}),
        ("t-obed", "Oběd varianty", "Kuřecí rizoto", {"kcal": 829, "b": 74}),
        ("t-obed", "Oběd varianty", "Těstoviny", {"kcal": 820, "b": 55}),
        ("t-snidane", "Snídaně", "Ovesná kaše", {"kcal": 447, "b": 30}),
        ("t-snidane", "Snídaně", "Toasty", {"kcal": 477, "b": 25}),
    ]
    db.replace_document_records(
        "d1",
        [
            {
                "id": f"r{i}", "table_id": tid, "page_no": 14, "category": cat,
                "name": name, "attrs_json": json.dumps(attrs), "raw_text": name,
            }
            for i, (tid, cat, name, attrs) in enumerate(data)
        ],
    )


def test_category_does_not_leak_across_generic_word(meals: None) -> None:
    """'Oběd varianty' nesmie pustiť 'Večeře varianty' (spoločné slovo 'varianty')."""
    db.insert_table("t-vecere", "d1", 16, "Večeře varianty", 1, 4, "/tmp/v.md", None)
    existing = db.list_records("d1")
    db.replace_document_records(
        "d1",
        [
            {k: r[k] for k in ("id", "table_id", "page_no", "category", "name",
                               "attrs_json", "raw_text")}
            for r in existing
        ]
        + [{
            "id": "rv1", "table_id": "t-vecere", "page_no": 16,
            "category": "Večeře varianty", "name": "Gnochi",
            "attrs_json": json.dumps({"kcal": 678, "b": 45}), "raw_text": "Gnochi",
        }],
    )
    plan = QueryPlan(
        mode="filter", constraints=[Constraint("kcal", "<=", 870)],
        categories=["Oběd varianty"],
    )
    matched, _ = run_filter(plan)
    assert all(h.category == "Oběd varianty" for h in matched)
    assert "Gnochi" not in [h.name for h in matched]


def test_run_filter_exact(meals: None) -> None:
    plan = QueryPlan(
        mode="filter",
        constraints=[Constraint("kcal", "<=", 870)],
        categories=["obed"],  # fuzzy trafí 'Oběd varianty'
    )
    matched, nearest = run_filter(plan)
    assert [h.name for h in matched] == ["Těstoviny", "Kuřecí rizoto"]
    assert nearest == []


def test_run_filter_zero_gives_nearest(meals: None) -> None:
    plan = QueryPlan(
        mode="filter",
        constraints=[Constraint("kcal", "<=", 100)],
        categories=["obed"],
    )
    matched, nearest = run_filter(plan)
    assert matched == []
    assert nearest, "pri nule musí ponúknuť najbližšie hodnoty"

    context, sources = build_filter_context(plan, matched, nearest)
    assert "ŽIADNA" in context
    assert sources and sources[0].filename == "jedla.pdf"


def test_filter_multi_constraint(meals: None) -> None:
    plan = QueryPlan(
        mode="filter",
        constraints=[Constraint("kcal", "<=", 820), Constraint("b", ">=", 50)],
        categories=[],
    )
    matched, _ = run_filter(plan)
    assert [h.name for h in matched] == ["Těstoviny"]


def test_pick_items_exact_combo(meals: None) -> None:
    combo = pick_items(
        [Slot("Oběd varianty", [Constraint("kcal", "<=", 870)]), Slot("Snídaně")],
        budget_attr="kcal",
        budget_max=1300,
        maximize="b",
    )
    assert combo is not None
    picks = dict(combo.picks)
    # obed ≤870 s max bielkovinami = Kuřecí rizoto (829, 74b); zvyšok 471 → Ovesná kaše
    assert picks["Oběd varianty"].name == "Kuřecí rizoto"
    assert picks["Snídaně"].name == "Ovesná kaše"
    assert combo.total["kcal"] == 829 + 447


def test_pick_items_impossible_budget_returns_nearest(meals: None) -> None:
    combo = pick_items(
        [Slot("Oběd varianty"), Slot("Snídaně")], budget_attr="kcal", budget_max=500
    )
    assert combo is not None
    assert combo.within_budget is False
    # najmenší možný súčet: Těstoviny 820 + Ovesná kaše 447
    assert combo.total["kcal"] == 820 + 447

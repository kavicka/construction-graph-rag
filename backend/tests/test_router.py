import json

import pytest

from app import db
from app.config import get_settings
from app.retrieve.router import route_query


@pytest.fixture
def meal_records() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "jedla.pdf", "sha-1")
    rows = [
        ("Oběd varianty", "Stir-fry", {"kcal": 829, "b": 74}),
        ("Oběd varianty", "Těstoviny", {"kcal": 820, "b": 55}),
        ("Snídaně", "Ovesná kaše", {"kcal": 447, "b": 30}),
    ]
    db.replace_document_records(
        "d1",
        [
            {
                "id": f"r{i}",
                "table_id": "t1",
                "page_no": 5,
                "category": cat,
                "name": name,
                "attrs_json": json.dumps(attrs),
                "raw_text": name,
            }
            for i, (cat, name, attrs) in enumerate(rows)
        ],
    )


def test_filter_query_sk(meal_records: None) -> None:
    plan = route_query("chcem obed do 870 kcal")
    assert plan.mode == "filter"
    assert plan.constraints[0].attr == "kcal"
    assert plan.constraints[0].op == "<="
    assert plan.constraints[0].value == 870
    assert plan.categories == ["Oběd varianty"]


def test_filter_with_protein_constraint(meal_records: None) -> None:
    plan = route_query("jedlo do 820 kalorii a nad 50g bielkovin")
    assert plan.mode == "filter"
    ops = {(c.attr, c.op) for c in plan.constraints}
    assert ("kcal", "<=") in ops
    assert ("b", ">=") in ops


def test_compose_query(meal_records: None) -> None:
    plan = route_query(
        "daj mi obed do 870 kcal a k tomu snídani, aby som sa spolu zmestil do 1500 kcal"
    )
    assert plan.mode == "compose"
    assert len(plan.categories) == 2


def test_lookup_without_numbers(meal_records: None) -> None:
    plan = route_query("ako pripravím stir-fry?")
    assert plan.mode == "lookup"
    assert plan.constraints == []

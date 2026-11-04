"""Query router beyond the basic filter/compose cases: deterministic mode precedence, custom
attributes from the record register, document scoping, library mentions and the LLM
fallback for numbers the heuristics cannot place."""

import json
from typing import Any

import pytest

from app import db
from app.config import get_settings
from app.graph.schema import CONCEPT
from app.llm.ollama_client import OllamaError
from app.retrieve import router
from app.retrieve.router import (
    _attr_vocab,
    _heuristic_constraints,
    _match_categories,
    _match_documents,
    _norm,
    _value,
    known_categories,
    mentions_library,
    route_query,
)


@pytest.fixture
def library() -> None:
    """Two documents: a menu (kcal/protein) and a price list with a custom `cena` attribute."""
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("menu", "Jedálniček týždeň.pdf", "sha-menu")
    db.insert_document("ceny", "Cenník betónu Agátová.pdf", "sha-ceny")
    rows = [
        ("menu", "Obed", "Gulášovka", {"kcal": 450, "b": 20}),
        ("menu", "Obed", "Rizoto", {"kcal": 800, "b": 40}),
        ("menu", "Raňajky", "Ovsená kaša", {"kcal": 300, "b": 12}),
        ("ceny", "Betón C25/30", "Betón C25", {"cena": 120.0, "hmotnost": 2400.0}),
        ("ceny", "Betón C30/37", "Betón C30", {"cena": 135.0, "hmotnost": 2450.0}),
    ]
    for doc_id in ("menu", "ceny"):
        db.replace_document_records(
            doc_id,
            [
                {
                    "id": f"{doc_id}-{i}",
                    "table_id": "t",
                    "page_no": 1,
                    "category": cat,
                    "name": name,
                    "attrs_json": json.dumps(attrs),
                    "raw_text": name,
                }
                for i, (d, cat, name, attrs) in enumerate(rows)
                if d == doc_id
            ],
        )
    db.replace_record_attrs(
        "ceny", [("cena", "Cena betónu", "EUR/m3"), ("hmotnost", "Hmotnosť", "kg")]
    )


# --- helpers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "expected"),
    [("870", 870.0), ("36,4", 36.4), ("36.4", 36.4), ("400 000", 400000.0), ("1 500,5", 1500.5)],
)
def test_value_parses_slovak_numbers(token: str, expected: float) -> None:
    assert _value(token) == expected


def test_norm_strips_diacritics_and_case() -> None:
    assert _norm("Šťavnatý OBED Ľubomír") == "stavnaty obed lubomir"


# --- precedence of the deterministic modes ---------------------------------------------


def test_generate_command_beats_numeric_filter(library: None) -> None:
    plan = route_query("Vytvor ponuku na obed do 500 kcal")
    assert plan.mode == "generate"
    assert plan.doc_type == "offer"
    assert plan.constraints == []  # the number is part of the document request, not a filter


def test_generate_english_sets_language(library: None) -> None:
    plan = route_query("Please draft a report about concrete prices")
    assert plan.mode == "generate" and plan.doc_type == "report" and plan.lang == "en"


def test_research_and_global_modes(library: None) -> None:
    assert route_query("/research riziká projektu Agátová").mode == "research"
    assert route_query("Aké sú hlavné témy všetkých dokumentov?").mode == "global"


def test_plain_question_is_lookup(library: None) -> None:
    plan = route_query("Kto je stavbyvedúci?")
    assert plan.mode == "lookup"
    assert plan.constraints == [] and plan.maximize is None


# --- heuristic constraints -------------------------------------------------------------


def test_upper_and_lower_bound_in_one_question(library: None) -> None:
    constraints = _heuristic_constraints(_norm("obed do 900 kcal a nad 30 g bielkovin"))
    assert {(c.attr, c.op, c.value) for c in constraints} == {
        ("kcal", "<=", 900.0),
        ("b", ">=", 30.0),
    }


def test_bare_upper_bound_with_kcal_in_text_defaults_to_kcal(library: None) -> None:
    constraints = _heuristic_constraints(_norm("ukaz jedla do 600, kcal rozhodujuce"))
    assert [(c.attr, c.op, c.value) for c in constraints] == [("kcal", "<=", 600.0)]


def test_bare_number_without_attribute_is_ignored(library: None) -> None:
    assert _heuristic_constraints(_norm("do 600 ludi")) == []


def test_upper_bound_falls_back_to_kcal_when_label_is_out_of_reach(library: None) -> None:
    # "kcal" is in the question, but too far from the number for the proximity scoring
    qn = _norm("do 600 zakazka na stavbe pri dome pre rodinu v meste: kcal")
    assert [(c.attr, c.op, c.value) for c in _heuristic_constraints(qn)] == [("kcal", "<=", 600.0)]
    # the kcal fallback is for upper bounds only
    lower = _norm("nad 600 zakazka na stavbe pri dome pre rodinu v meste: kcal")
    assert _heuristic_constraints(lower) == []


def test_thousands_separator_number(library: None) -> None:
    constraints = _heuristic_constraints(_norm("betón pod 1 500 kcal"))
    assert constraints and constraints[0].value == 1500.0


def test_custom_attribute_from_register_by_unit_and_label(library: None) -> None:
    by_unit = _heuristic_constraints(_norm("betón do 130 EUR/m3"))
    assert [(c.attr, c.op, c.value) for c in by_unit] == [("cena", "<=", 130.0)]
    by_label = _heuristic_constraints(_norm("s hmotnostou nad 2420"))
    assert ("hmotnost", ">=", 2420.0) in {(c.attr, c.op, c.value) for c in by_label}


def test_attr_vocab_contains_fixed_macros_and_register(library: None) -> None:
    vocab = _attr_vocab()
    assert {"kcal", "b", "s", "t", "cena", "hmotnost"} <= set(vocab)
    stems, units = vocab["cena"]
    assert units == ("eur/m3",) and any(s.startswith("cena") for s in stems)
    assert vocab["kcal"][1] == ()  # built-in macros have no unit pattern


def test_register_attribute_does_not_override_builtin_macro(library: None) -> None:
    db.replace_record_attrs("menu", [("kcal", "Energia podľa dokumentu", "kJ")])
    assert _attr_vocab()["kcal"][1] == ()


# --- maximize hints and compose/filter decision ----------------------------------------


def test_maximize_hint_alone_makes_a_filter_plan(library: None) -> None:
    plan = route_query("obed s najviac bielkovinami")
    assert plan.mode == "filter"
    assert plan.maximize == "b"
    assert plan.categories == ["Obed"]


def test_two_categories_make_compose(library: None) -> None:
    plan = route_query("obed a raňajky do 1000 kcal")
    assert plan.mode == "compose"
    assert set(plan.categories) == {"Obed", "Raňajky"}


def test_two_upper_bounds_on_one_category_compose(library: None) -> None:
    plan = route_query("obed do 900 kcal a do 40 g bielkovin")
    assert plan.mode == "compose"


def test_two_upper_bounds_without_category_stay_filter(library: None) -> None:
    plan = route_query("jedlo do 900 kcal a do 40 g bielkovin")
    assert plan.mode == "filter"
    assert plan.categories == []


# --- document scoping ------------------------------------------------------------------


def test_filter_without_category_is_scoped_to_mentioned_document(library: None) -> None:
    plan = route_query("v cenníku Agátová pod 130 EUR/m3")
    assert plan.mode == "filter"
    assert plan.doc_ids == ["ceny"]


def test_document_scope_is_dropped_when_a_category_matches(library: None) -> None:
    plan = route_query("Betón C30 v cenníku Agátová do 140 EUR/m3")
    assert plan.categories  # the category narrows the search already
    assert plan.doc_ids == []


def test_match_documents_tolerates_inflection_and_diacritics() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("d1", "Rozpočet Agátová.pdf", "s1")
    db.insert_document("d2", "Iný dokument.pdf", "s2")
    assert _match_documents(_norm("v rozpočte Agátovej ulice")) == ["d1"]  # Agátovej ↔ Agátová
    assert _match_documents(_norm("nič z toho")) == []


def test_match_documents_prefers_best_scoring_documents() -> None:
    get_settings().ensure_dirs()
    db.init_db()
    db.insert_document("a", "Rozpočet Agátová.pdf", "s1")
    db.insert_document("b", "Rozpočet Rovinka.pdf", "s2")
    assert _match_documents(_norm("rozpočet Agátová")) == ["a"]  # two tokens beat one


# --- categories and library mentions ---------------------------------------------------


def test_known_categories_sorted_and_distinct(library: None) -> None:
    assert known_categories() == ["Betón C25/30", "Betón C30/37", "Obed", "Raňajky"]


def test_match_categories_is_fuzzy_on_long_tokens(library: None) -> None:
    assert _match_categories(_norm("ake su ranajky")) == ["Raňajky"]
    assert _match_categories(_norm("co je v oboch")) == []  # short tokens (<4) never match


def test_mentions_library_by_category(library: None) -> None:
    assert mentions_library("Čo máme na obed?") is True


def test_mentions_library_by_document_name(library: None) -> None:
    assert mentions_library("Čo hovorí cenník Agátová?") is True


def test_mentions_library_by_graph_concept(library: None) -> None:
    db.upsert_graph_node("c1", CONCEPT, "zmluvná pokuta", norm_key="zmluvna pokuta")
    db.upsert_graph_node("c2", CONCEPT, "NP", norm_key="np")
    assert mentions_library("Aká je zmluvná pokuta pri omeškaní?") is True
    assert mentions_library("Poznáš NP?") is False  # labels shorter than 5 chars are too noisy


def test_mentions_library_false_for_unrelated_question(library: None) -> None:
    assert mentions_library("Aké je dnes počasie v Londýne?") is False


# --- LLM fallback ----------------------------------------------------------------------


def _llm(monkeypatch: pytest.MonkeyPatch, reply: str | Exception) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(model: str, prompt: str, **kw: Any) -> str:
        calls.append({"model": model, "prompt": prompt, **kw})
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(router, "generate_sync", fake)
    return calls


# "400 g" has no recognised operator, so the heuristics find nothing and the LLM is asked
QUESTION = "obed s 400 g bielkovín"


def test_llm_fallback_builds_filter_plan(library: None, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _llm(
        monkeypatch,
        json.dumps(
            {
                "mode": "filter",
                "constraints": [{"attr": "b", "op": ">=", "value": 40}],
            }
        ),
    )
    plan = route_query(QUESTION)
    assert plan.mode == "filter"
    assert [(c.attr, c.op, c.value) for c in plan.constraints] == [("b", ">=", 40.0)]
    assert plan.categories == ["Obed"]  # categories still come from the data-driven matcher
    assert calls and calls[0]["model"] == get_settings().extract_model
    assert QUESTION in calls[0]["prompt"]
    assert calls[0]["json_schema"]["properties"]["mode"]


def test_llm_fallback_not_called_without_a_number(
    library: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _llm(monkeypatch, json.dumps({"mode": "filter", "constraints": []}))
    assert route_query("obed s bielkovinami").mode == "lookup"
    assert calls == []


def test_llm_fallback_not_called_when_heuristics_succeed(
    library: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _llm(monkeypatch, "{}")
    assert route_query("obed do 900 kcal").mode == "filter"
    assert calls == []


@pytest.mark.parametrize(
    "reply",
    [
        OllamaError("down"),
        "not json at all",
        json.dumps({"mode": "unknown", "constraints": []}),  # fails schema validation
        json.dumps({"mode": "lookup", "constraints": [{"attr": "b", "op": ">=", "value": 1}]}),
        json.dumps({"mode": "filter", "constraints": []}),
    ],
)
def test_llm_fallback_failures_degrade_to_lookup(
    library: None, monkeypatch: pytest.MonkeyPatch, reply: str | Exception
) -> None:
    _llm(monkeypatch, reply)
    plan = route_query(QUESTION)
    assert plan.mode == "lookup"
    assert plan.constraints == []
    assert plan.categories == ["Obed"]


def test_llm_fallback_drops_invalid_constraints(
    library: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _llm(
        monkeypatch,
        json.dumps(
            {
                "mode": "compose",
                "constraints": [
                    {"attr": "unknown_attr", "op": "<=", "value": 5},  # not a macro
                    {"attr": "kcal", "op": "!=", "value": 5},  # bad operator
                    {"attr": "kcal", "op": "<=", "value": "abc"},  # not a number
                    {"attr": "kcal", "op": "<", "value": "800"},  # valid, numeric string
                ],
            }
        ),
    )
    plan = route_query(QUESTION)
    assert plan.mode == "compose"
    assert [(c.attr, c.op, c.value) for c in plan.constraints] == [("kcal", "<", 800.0)]


def test_llm_fallback_with_only_invalid_constraints_is_lookup(
    library: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _llm(
        monkeypatch,
        json.dumps(
            {
                "mode": "filter",
                "constraints": [
                    {"attr": "zzz", "op": "<=", "value": 1},  # unknown attribute
                    {"op": "<=", "value": 5},  # missing attribute
                ],
            }
        ),
    )
    assert route_query(QUESTION).mode == "lookup"

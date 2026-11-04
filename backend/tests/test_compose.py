import json

import pytest

from app import db
from app.auth import AccessScope, reset_scope, set_scope
from app.config import get_settings
from app.retrieve.compose import extract_compose_plan, run_compose
from app.retrieve.orchestrator import answer_question
from app.retrieve.router import QueryPlan, known_categories
from app.workforce import store

KNOWN = ["Oběd varianty", "Snídaně"]

VALID_PLAN = json.dumps(
    {
        "slots": [
            {"category": "obed", "max_value": 870},
            {"category": "snidane", "max_value": None},
        ],
        "budget_attr": "kcal",
        "budget_max": 1500,
        "maximize": "b",
    }
)

QUESTION = "daj mi obed do 870 kcal a ranajky, spolu do 1500 kcal, co najviac bielkovin"


def test_plan_maps_categories_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: VALID_PLAN)
    plan = extract_compose_plan(QUESTION, KNOWN)
    assert plan is not None
    assert [s.category for s in plan.slots] == ["Oběd varianty", "Snídaně"]
    assert plan.budget_max == 1500
    assert plan.slots[0].max_value == 870


def test_plan_rejects_invented_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    invented = VALID_PLAN.replace("1500", "1400")  # model si „dopočítal" budget
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: invented)
    assert extract_compose_plan(QUESTION, KNOWN) is None


def test_plan_sanitizes_invented_slot_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model dopočítal slot limit (1500-870=630) → limit sa zmaže, plán prežije."""
    invented = VALID_PLAN.replace('"max_value": null', '"max_value": 630')
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: invented)
    plan = extract_compose_plan(QUESTION, KNOWN)
    assert plan is not None
    assert plan.slots[0].max_value == 870  # legitímny limit z otázky ostal
    assert plan.slots[1].max_value is None  # vymyslený 630 zmazaný


class FakeEmbedder:
    model_name = "fake"

    def _vec(self, text: str) -> list[float]:
        import unicodedata

        nfkd = unicodedata.normalize("NFKD", text.casefold())
        t = "".join(c for c in nfkd if not unicodedata.combining(c))
        if "ranajk" in t or "snidan" in t:
            return [1.0, 0.0]
        return [0.0, 1.0]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def test_cross_language_category_via_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    """SK 'raňajky' → CZ 'Snídaně' — lexika zlyhá, embedding mapuje."""
    monkeypatch.setattr("app.index.embedder.get_embedder", FakeEmbedder)
    plan_json = VALID_PLAN.replace('"category": "snidane"', '"category": "raňajky"')
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: plan_json)
    plan = extract_compose_plan(QUESTION, ["Oběd varianty", "Snídaně"])
    assert plan is not None
    assert plan.slots[1].category == "Snídaně"


def test_plan_rejects_unknown_category(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = VALID_PLAN.replace("obed", "vecere-neexistuju")
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: bad)
    assert extract_compose_plan(QUESTION, KNOWN) is None


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
        ("t-snidane", "Snídaně", "Ovesná kaše", {"kcal": 447, "b": 30}),
        ("t-snidane", "Snídaně", "Toasty", {"kcal": 477, "b": 25}),
    ]
    db.replace_document_records(
        "d1",
        [
            {
                "id": f"r{i}",
                "table_id": tid,
                "page_no": 14,
                "category": cat,
                "name": name,
                "attrs_json": json.dumps(attrs),
                "raw_text": name,
            }
            for i, (tid, cat, name, attrs) in enumerate(data)
        ],
    )


def test_run_compose_end_to_end(meals: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: VALID_PLAN)
    result = run_compose(QUESTION, KNOWN)
    assert result is not None
    answer, sources, evidence = result
    # rizoto (829, 74b) + kaše (447, 30b) = 1276 ≤ 1500, max bielkoviny
    assert "Kuřecí rizoto" in answer
    assert "Ovesná kaše" in answer
    assert "1276" in answer
    assert sources and {s.filename for s in sources} == {"jedla.pdf"}
    assert QUESTION in evidence


def test_run_compose_impossible_shows_nearest(meals: None, monkeypatch: pytest.MonkeyPatch) -> None:
    tight = VALID_PLAN.replace("1500", "1000")
    monkeypatch.setattr("app.retrieve.compose.generate_sync", lambda *a, **kw: tight)
    result = run_compose(QUESTION.replace("1500", "1000"), KNOWN)
    assert result is not None
    answer, sources, _ = result
    assert "NEDÁ splniť" in answer
    assert "najbližšia" in answer
    # min kombinácia pod obed≤870: rizoto(829)+kaše(447)=1276 → o 276 nad 1000
    assert "1276" in answer
    assert "276" in answer
    assert sources, "aj najbližšia kombinácia má citovateľné zdroje"


HIDDEN_CATEGORY = "Tajný rozpočet"
MEMBER = AccessScope(user_id="u1", email="m@x.sk", role="member", groups=frozenset({"stavba"}))


@pytest.fixture
def hidden_records(meals: None) -> None:
    """d2 is visible only to the group "financie" — the member is not in it."""
    store.init_workforce_db()  # the orchestrator asks the Firma module first
    db.insert_document("d2", "financie.pdf", "sha-2")
    db.set_document_access("d2", json.dumps(["financie"]))
    db.replace_document_records(
        "d2",
        [
            {
                "id": "secret",
                "table_id": "t-secret",
                "page_no": 1,
                "category": HIDDEN_CATEGORY,
                "name": "Rezerva",
                "attrs_json": json.dumps({"kcal": 1}),
                "raw_text": "Rezerva",
            }
        ],
    )


def test_known_categories_follow_access_scope(hidden_records: None) -> None:
    assert known_categories() == ["Oběd varianty", "Snídaně", HIDDEN_CATEGORY]  # admin
    token = set_scope(MEMBER)
    try:
        assert known_categories() == ["Oběd varianty", "Snídaně"]
    finally:
        reset_scope(token)


@pytest.mark.anyio
async def test_compose_prompt_hides_categories_of_inaccessible_documents(
    hidden_records: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []

    def fake_generate(model: str, prompt: str, **kwargs: object) -> str:
        prompts.append(prompt)
        return VALID_PLAN

    monkeypatch.setattr("app.retrieve.compose.generate_sync", fake_generate)
    monkeypatch.setattr(
        "app.retrieve.orchestrator.route_query", lambda q: QueryPlan(mode="compose")
    )
    token = set_scope(MEMBER)
    try:
        result = await answer_question(QUESTION)
    finally:
        reset_scope(token)
    assert result.mode == "compose"
    assert len(prompts) == 1
    assert "Oběd varianty" in prompts[0]
    assert HIDDEN_CATEGORY not in prompts[0]

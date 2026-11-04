import math

import pytest

from eval.construction import CORPUS, QUESTIONS
from eval.metrics import (
    RetrievedChunk,
    citation_validity,
    contains_expected,
    is_refusal,
    mean,
    ndcg_at_k,
    normalize_answer,
    recall_at_k,
    reciprocal_rank,
)


def _c(doc: str, p_from: int, p_to: int | None = None) -> RetrievedChunk:
    return RetrievedChunk(doc, p_from, p_to or p_from)


def test_normalize_numbers_and_dates() -> None:
    assert normalize_answer("Cena je 4 860 000 EUR") == "cena je 4860000 eur"
    assert normalize_answer("1 742 300") == "1742300"
    assert normalize_answer("pokuta 0,05 %") == "pokuta 0.05 %"
    assert normalize_answer("do 30. 11. 2027") == "do 30.11.2027"
    assert normalize_answer("Šimková") == "simkova"


@pytest.mark.parametrize(
    ("answer", "expected", "ok"),
    [
        ("Cena diela je 4 860 000 EUR [1].", "4 860 000", True),
        ("Cena diela je 4860000 EUR.", "4 860 000", True),
        ("Pokuta je 0.05 % denne.", "0,05", True),
        ("Termín je 30.11.2027.", "30. 11. 2027", True),
        ("Zádržné je 5 %.", "5", True),
        ("Zádržné je 15 %.", "5", False),  # short token only as a whole word
        ("Pevnosť 36.4 MPa", "36.4", True),
        ("Pevnosť 36.45 MPa", "36.4", False),
        ("Trieda E.", "E", True),
        ("Energetická trieda A0.", "E", False),
        ("Cieľ je trieda A0", "A0", True),
        ("The roof costs 386,500 EUR.", "386 500", True),
        ("Pokuta 0,05 %.", "0,05", True),
        ("Prehliadka bola 4. augusta 2026.", "4 August 2026|4. augusta 2026", True),
    ],
)
def test_contains_expected(answer: str, expected: str, ok: bool) -> None:
    assert contains_expected(answer, expected) is ok


def test_refusal_detection() -> None:
    assert is_refusal("Táto informácia sa v podkladoch nenachádza.")
    assert is_refusal("The sources do not mention the CEO.")
    assert not is_refusal("Cena je 4 860 000 EUR [1].")
    assert is_refusal("V zdrojoch nie je informácia o vzorke K-06. [1]")  # superfluous citation
    # a long answer missing just one part is not a refusal
    assert not is_refusal("Pokuta Agátová je 0,05 % denne [1]. " * 8 + "Senec neuvádza.")


def test_retrieval_metrics() -> None:
    gold = [("a.pdf", 2), ("b.pdf", 1)]
    ranked = [_c("c.pdf", 1), _c("a.pdf", 1, 2), _c("a.pdf", 2), _c("b.pdf", 1)]
    assert recall_at_k(ranked, gold, 1) == 0.0
    assert recall_at_k(ranked, gold, 2) == 0.5
    assert recall_at_k(ranked, gold, 4) == 1.0
    assert reciprocal_rank(ranked, gold) == 0.5
    # gains at positions 2 and 4 (position 3 covers an already seen page)
    expected = (1 / math.log2(3) + 1 / math.log2(5)) / (1 + 1 / math.log2(3))
    assert ndcg_at_k(ranked, gold, 10) == pytest.approx(expected)
    assert math.isnan(recall_at_k(ranked, [], 5))
    assert reciprocal_rank([_c("x.pdf", 1)], gold) == 0.0


def test_mean_ignores_nan() -> None:
    assert mean([1.0, math.nan, 0.0]) == 0.5
    assert math.isnan(mean([math.nan]))


def test_citation_validity() -> None:
    assert citation_validity("Text [1] a [2, 3].", 3) is True
    assert citation_validity("Text [4].", 3) is False
    assert citation_validity("Bez citácie.", 3) is None


def test_golden_set_is_consistent() -> None:
    pages = {d.filename: len(d.pages) for d in CORPUS}
    ids = [q.id for q in QUESTIONS]
    assert len(ids) == len(set(ids)), "duplicitné id otázok"
    assert len(QUESTIONS) >= 100
    for q in QUESTIONS:
        for doc, page in q.gold:
            assert doc in pages, f"{q.id}: neznámy dokument {doc}"
            assert 1 <= page <= pages[doc], f"{q.id}: strana {page} mimo {doc}"
        if q.kind == "unanswerable":
            assert not q.gold
        else:
            assert q.gold, q.id
    kinds = {q.kind for q in QUESTIONS}
    assert kinds == {"factual", "table", "cross_doc", "multi_hop", "unanswerable"}

import pytest

from app.retrieve.pipeline import Source
from app.retrieve.verify import split_sentences, verify_sentences


class KeywordEmbedder:
    """Deterministic embedder: vector = occurrence of keywords."""

    KEYS = ("cena", "pokuta", "stavbyved", "hronec", "betón", "lešen", "2030", "holcim")

    def _vec(self, text: str) -> list[float]:
        t = text.casefold()
        return [1.0 if k in t else 0.0 for k in self.KEYS] + [0.1]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def _src(n: int, text: str) -> Source:
    return Source(
        n=n,
        chunk_id=f"c{n}",
        doc_id="d",
        filename="z.pdf",
        page_from=1,
        page_to=1,
        type="text",
        table_id=None,
        text=text,
        snippet=text[:50],
    )


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.retrieve.verify.get_embedder", KeywordEmbedder)


def test_split_sentences_keeps_abbreviations_and_ordinals() -> None:
    text = (
        "Stavbyvedúcim je Ing. Peter Hronec [1]. Strop nad 2. NP sa betónoval 15. 6. 2026. "
        "Firma Stavby Kráľ s.r.o. Žilina dodá dielo.\n- bod jeden\n- bod dva"
    )
    assert split_sentences(text) == [
        "Stavbyvedúcim je Ing. Peter Hronec [1].",
        "Strop nad 2. NP sa betónoval 15. 6. 2026.",
        "Firma Stavby Kráľ s.r.o. Žilina dodá dielo.",
        "bod jeden",
        "bod dva",
    ]


def test_statuses() -> None:
    sources = [
        _src(1, "Cena diela je 4 860 000 EUR bez DPH. Zmluvná pokuta je 0,05 % denne."),
        _src(2, "Stavbyvedúcim zhotoviteľa je Ing. Peter Hronec."),
    ]
    answer = (
        "Cena diela je 4 860 000 EUR bez DPH [1]. "
        "Zmluvná pokuta je 0,07 % z ceny za deň [1]. "  # number doesn't match
        "Stavbyvedúcim zhotoviteľa je Ing. Peter Hronec [2]. "
        "Betón dodáva firma Holcim zo Senca [2]. "  # the source says nothing about it
        "Dielo bude odovzdané v roku 2030 bez citácie. "
        "Podklady neuvádzajú, kto dodá výťah."  # a refusal is not evaluated
    )
    checks = verify_sentences(answer, sources)
    assert [(c.status, c.citations) for c in checks] == [
        ("supported", [1]),
        ("weak", [1]),
        ("supported", [2]),
        ("weak", [2]),
        ("uncited", []),
    ]
    assert checks[0].score == pytest.approx(1.0)


def test_citation_to_missing_source_is_uncited() -> None:
    checks = verify_sentences("Cena diela je 4 860 000 EUR bez DPH [7].", [_src(1, "Cena.")])
    assert checks[0].status == "uncited"

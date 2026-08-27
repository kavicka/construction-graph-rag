from app.retrieve.selfcheck import check_numbers


def test_numbers_from_evidence_pass() -> None:
    evidence = ["Těstoviny: kcal=820, b=55", "Kuřecí rizoto: kcal=829, b=74"]
    assert check_numbers("Těstoviny majú 820 kcal a 55 g bielkovín [1].", evidence) == []


def test_hallucinated_number_flagged() -> None:
    evidence = ["Těstoviny: kcal=820"]
    issues = check_numbers("Jedlo má 999 kcal.", evidence)
    assert issues == ["999"]


def test_citations_and_small_numbers_ignored() -> None:
    evidence = ["kcal=820"]
    assert check_numbers("Vyber si položku 3, má 820 kcal [7].", evidence) == []


def test_pair_sums_allowed() -> None:
    evidence = ["obed kcal=829", "snidane kcal=447"]
    assert check_numbers("Spolu 1276 kcal.", evidence) == []
    assert check_numbers("Spolu 1300 kcal.", evidence) == ["1300"]

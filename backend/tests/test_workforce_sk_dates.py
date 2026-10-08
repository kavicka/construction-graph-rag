from datetime import date

import pytest

from app.workforce.sk_dates import parse_term

REF = date(2026, 10, 7)  # streda


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("do 20. 10. 2026", None, date(2026, 10, 20)),
        ("do 20.10.", None, date(2026, 10, 20)),
        ("15.–18. 10.", date(2026, 10, 15), date(2026, 10, 18)),
        ("od 12.10. do 16.10.2026", date(2026, 10, 12), date(2026, 10, 16)),
        ("12. 10. 2026", date(2026, 10, 12), date(2026, 10, 12)),
        ("od 2026-10-19", date(2026, 10, 19), None),
        ("do konca mesiaca", None, date(2026, 10, 31)),
        ("do konca týždňa", None, date(2026, 10, 9)),
        ("budúci týždeň", date(2026, 10, 12), date(2026, 10, 16)),
        ("v 43. týždni", date(2026, 10, 19), date(2026, 10, 23)),
        ("KW 44", date(2026, 10, 26), date(2026, 10, 30)),
        ("o dva týždne", None, date(2026, 10, 21)),
        ("do 5 dní", None, date(2026, 10, 12)),
        ("v novembri", date(2026, 11, 1), date(2026, 11, 30)),
        ("v pondelok", date(2026, 10, 12), date(2026, 10, 12)),
        ("do piatku", None, date(2026, 10, 9)),
        ("zajtra", date(2026, 10, 8), date(2026, 10, 8)),
        ("5. januára", date(2027, 1, 5), date(2027, 1, 5)),
        ("termín dokončenia 30. novembra", None, date(2026, 11, 30)),
    ],
)
def test_parse_term(text: str, start: date | None, end: date | None) -> None:
    r = parse_term(text, REF)
    assert (r.date_from, r.date_to) == (start, end), text


def test_unknown_term() -> None:
    assert parse_term("čo najskôr", REF).how == "none"
    assert parse_term(None, REF).how == "none"
    assert parse_term("po dokončení základov", REF).how == "none"

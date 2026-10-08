import pytest

from app.workforce.sk_numbers import (
    hours_supported,
    is_short_negative,
    minutes_supported,
    number_candidates,
    percent_supported,
    time_candidates,
    time_supported,
    to_24h,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("prišiel som o pol ôsmej", (7, 30)),
        ("prišiel som o pol uosmej", (7, 30)),  # chyba prepisu
        ("o siedmej", (7, 0)),
        ("odišiel som o 4", (4, 0)),
        ("začal som 6:45", (6, 45)),
        ("koniec 15.30", (3, 30)),
        ("o štvrť na päť", (4, 15)),
        ("o tri štvrte na osem", (7, 45)),
        ("od siedmej hodiny", (7, 0)),
        ("o pol 8", (7, 30)),
    ],
)
def test_time_candidates(text: str, expected: tuple[int, int]) -> None:
    assert expected in time_candidates(text)


def test_time_supported_rejects_invented() -> None:
    text = "Dnes som prišiel o pol ôsmej a robil som murovanie."
    assert time_supported("07:30", text)
    assert not time_supported("08:30", text)  # klasická chyba: „pol ôsmej“ ≠ 8:30
    assert not time_supported("16:00", text)  # odchod nepovedal


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("robil som na tom päť hodín", 5),
        ("tri hodiny osádzal rámy", 3),
        ("dve a pol hodiny", 2.5),
        ("5 a pol", 5.5),
        ("hodinu som upratoval", 1),
        ("asi polhodinu", 0.5),
        ("dvadsaťpäť", 25),
        ("dvadsať päť", 25),
    ],
)
def test_number_candidates(text: str, value: float) -> None:
    assert value in number_candidates(text)


def test_hours_from_time_range() -> None:
    assert hours_supported(5, "murovali sme od 7 do 12")
    assert hours_supported(3.5, "od pol ôsmej do jedenástej")
    assert not hours_supported(6, "murovali sme od 7 do 12")


def test_minutes_and_percent() -> None:
    assert minutes_supported(30, "prestávka bola pol hodiny")
    assert minutes_supported(15, "15 minút")
    assert minutes_supported(60, "mali sme hodinu obed")
    assert not minutes_supported(45, "prestávka bola pol hodiny")
    assert percent_supported(50, "spravili sme to asi na polovicu")
    assert percent_supported(80, "hotové na 80 %")
    assert not percent_supported(70, "hotové na 80 %")


def test_short_negative() -> None:
    assert is_short_negative("nie")
    assert is_short_negative("Nie, všetko v poriadku.")
    assert is_short_negative("žiadne problémy")
    assert not is_short_negative("nebolo lepidlo")
    assert not is_short_negative("nie, ale pokazil sa bager a dve hodiny sme čakali na opravu")


def test_to_24h() -> None:
    assert to_24h((4, 0), afternoon_hint=True) == (16, 0)
    assert to_24h((7, 30), afternoon_hint=False) == (7, 30)
    assert to_24h((15, 30), afternoon_hint=True) == (15, 30)
    assert to_24h((11, 0), afternoon_hint=True) == (11, 0)


def test_conjunction_a_before_half_and_glued_preposition() -> None:
    # „a“ ako spojka nezruší polhodinu; „Opol 7“ z Whispera = o pol siedmej
    assert number_candidates("hodinu sme čakali na piesok a pol hodiny som upratoval") == {1.0, 0.5}
    assert number_candidates("dve a pol hodiny") == {2.5}
    assert time_candidates("Opol 7.") == {(6, 30)}


def test_stt_artifacts_in_times() -> None:
    assert time_candidates("od polu osmej do štvrtej") == {(7, 30), (4, 0)}
    assert time_candidates("o štvrti na sedem a o tri štvrte na štyri") == {(6, 15), (3, 45)}

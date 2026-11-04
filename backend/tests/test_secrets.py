"""Redakcia tajomstiev — vzory, návestia, falošné poplachy a priebežný stream (PLAN.md §17)."""

import itertools

import pytest

from app.auth import hash_password
from app.retrieve.scope_guard import SECRET_REPLY
from app.retrieve.secrets import MASK, SecretStreamFilter, contains_secret, redact_secrets

SHAPES = [
    hash_password("tajne-heslo"),
    "rag_" + "A1b2C3d4" * 5,
    "sk-proj-" + "x9" * 15,
    "ghp_" + "a" * 36,
    "AKIA" + "ABCDEFGHIJKLMNOP",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "a3f1" * 16,
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
]


@pytest.mark.parametrize("secret", SHAPES)
def test_shapes_are_masked(secret: str) -> None:
    text = f"Konfigurácia: {secret} (platí do konca roka)"
    out = redact_secrets(text)
    assert secret not in out and MASK in out
    assert out.startswith("Konfigurácia: ") and out.endswith("(platí do konca roka)")


def test_bearer_token() -> None:
    out = redact_secrets("Authorization: Bearer abcDEF123456789xyz0987654321")
    assert "abcDEF123456789xyz0987654321" not in out


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Heslo k WiFi na stavbe: Kral-2026", "Kral-2026"),
        ("heslo: stavba", "stavba"),
        ("PIN 4821", "4821"),
        ("PIN: 482193", "482193"),
        ("Kód od brány: 7731#", "7731#"),
        ("Prístupový kód k alarmu = 99-12-07", "99-12-07"),
        ("api_key=abc123XYZ", "abc123XYZ"),
        ("API kľúč: sk_live_51Hx", "sk_live_51Hx"),
        ("heslo je Kral2026!", "Kral2026"),
        ("The password is Summer2026", "Summer2026"),
        ("Heslo:\nBrana-7731", "Brana-7731"),
        ("WPA kľúč „Stavba#2026“", None),  # bez oddeľovača — nejednoznačné, nevadí
    ],
)
def test_label_values(text: str, secret: str | None) -> None:
    out = redact_secrets(text)
    if secret is not None:
        assert secret not in out, out
        assert MASK in out


@pytest.mark.parametrize(
    "text",
    [
        "IBAN: SK31 1200 0000 1987 4263 7541",
        "Rodné číslo: 850101/1234, tel. +421 905 123 456",
        "Príchod 6:30, odchod 15:00, prestávka 30 min, odpracované 8 h",
        "Cena diela je 4 860 000 EUR bez DPH.",
        "Heslo je potrebné zmeniť každé 3 mesiace.",
        "Projekt Heslo roka 2026 vyhral tím z Rovinky.",
        SECRET_REPLY,
        "PIN-y nastavuje administrátor v časti Tím.",
        "Tokeny a kľúče sa v systéme neukladajú v čitateľnej podobe.",
    ],
)
def test_no_false_positives(text: str) -> None:
    assert redact_secrets(text) == text


def test_idempotent() -> None:
    once = redact_secrets("Heslo k WiFi: Kral-2026, PIN 4821, token " + "f" * 40)
    assert redact_secrets(once) == once
    assert not contains_secret(once)


def _stream(tokens: list[str]) -> list[str]:
    f = SecretStreamFilter()
    out = [f.feed(t) for t in tokens]
    out.append(f.flush())
    return out


def test_stream_never_leaks_split_value() -> None:
    tokens = [
        "Na stavbe ",
        "je WiFi. ",
        "Heslo",
        " k",
        " WiFi",
        ":",
        " Kr",
        "al-",
        "20",
        "26",
        " a platí",
        " celý",
        " mesiac.",
    ]
    chunks = _stream(tokens)
    assert all("Kral" not in c and "2026" not in c for c in chunks)
    assert "".join(chunks) == redact_secrets("".join(tokens))


def test_stream_shape_secret_split_across_tokens() -> None:
    secret = "rag_" + "Q" * 30
    tokens = ["Token ", secret[:10], secret[10:25], secret[25:], " je neplatný."]
    joined = "".join(_stream(tokens))
    assert secret not in joined and MASK in joined


@pytest.mark.parametrize("size", [1, 2, 3, 5, 8, 13])
def test_stream_matches_full_redaction(size: int) -> None:
    text = (
        "Kontakt: stavbyvedúci Ing. Peter Hronec. PIN 4821 si nikomu nedávaj. "
        "Heslo k bráne: Brana-7731 platí do piatku.\nIBAN SK31 1200 0000 1987 4263 7541."
    )
    tokens = ["".join(chunk) for chunk in itertools.batched(text, size)]
    joined = "".join(_stream(tokens))
    assert "4821" not in joined and "Brana-7731" not in joined
    assert "SK31 1200 0000 1987 4263 7541" in joined

import pytest
from fastapi.testclient import TestClient

from app import db
from app.company import load_company
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_defaults_then_roundtrip(client: TestClient) -> None:
    with client:
        initial = client.get("/api/company").json()
        assert initial["name"] == ""
        assert initial["show_page_numbers"] is True
        # vzhľad (logo, farby, font) nie je v nastaveniach — dáva ho téma aplikácie
        assert not {"logo", "has_logo", "primary_color", "accent_color", "font"} & set(initial)

        payload = {
            **initial,
            "name": "Stavby Kráľ s.r.o.",
            "ico": "123 456 78",
            "iban": "sk31 1200 0000 1987 4263 7541",
            "footer_text": "Zapísaná v OR OS Žilina",
        }
        saved = client.put("/api/company", json=payload)
        assert saved.status_code == 200
        body = saved.json()
        assert body["ico"] == "12345678"  # medzery preč
        assert body["iban"] == "SK3112000000198742637541"

        again = client.get("/api/company").json()
        assert again["name"] == "Stavby Kráľ s.r.o."
        assert again["footer_text"] == "Zapísaná v OR OS Žilina"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ico", "12ab5678"),
        ("email", "bez-zavinaca"),
        ("iban", "1234"),
        ("name", "x" * 121),
    ],
)
def test_validation_rejects(client: TestClient, field: str, value: str) -> None:
    with client:
        current = client.get("/api/company").json()
        current[field] = value
        assert client.put("/api/company", json=current).status_code == 422


def test_legacy_branding_fields_are_ignored(client: TestClient) -> None:
    """Uložené nastavenia z čias, keď tu bolo logo/farby/font, sa načítajú bez nich."""
    with client:
        client.get("/api/company")  # init DB
        legacy = (
            '{"name": "Stará firma", "primary_color": "#112233", "accent_color": "#d97706",'
            ' "font": "lora"}'
        )
        with db.get_conn() as conn:
            conn.execute("INSERT INTO company_settings (id, data_json) VALUES (1, ?)", (legacy,))
        assert load_company().name == "Stará firma"
        body = client.get("/api/company").json()
        assert body["name"] == "Stará firma" and "font" not in body


def test_removed_branding_endpoints_are_gone(client: TestClient) -> None:
    with client:
        for path in ("/api/company/logo", "/api/company/fonts", "/api/company/palette"):
            assert client.get(path).status_code in (404, 405)


def test_preview_renders_pdf(client: TestClient) -> None:
    with client:
        settings = client.get("/api/company").json()
        settings["name"] = "Ťažká Žltá s.r.o."
        resp = client.post("/api/company/preview", json=settings)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")
        # náhľad nič neukladá
        assert client.get("/api/company").json()["name"] == ""


def test_health_reports_pdf_engine(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_ollama(base_url: str, wanted: list[str]) -> object:
        from app.api.health import OllamaStatus

        return OllamaStatus(up=False, models=dict.fromkeys(wanted, False))

    monkeypatch.setattr("app.api.health.check_ollama", fake_ollama)
    with client:
        assert client.get("/api/health").json()["pdf_engine"] is True

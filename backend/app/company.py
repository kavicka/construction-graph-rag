"""Nastavenia firmy — údaje a hlavička/pätička generovaných dokumentov.

Jeden riadok v SQLite (`company_settings`, id = 1) s JSON payloadom. Vzhľad dokumentov
(farby, písmo) tu NIE je — určuje ho téma aplikácie (`app.docgen.theme`). Staré uložené
nastavenia s poľami logo/farby/font sa načítajú bez nich (neznáme polia sa ignorujú).
Validácia je v Pydantic modeli, DB vrstva je hlúpa.
"""

import json

from pydantic import BaseModel, Field, field_validator

from app.db import get_conn
from app.docgen.theme import THEME


class CompanySettings(BaseModel):
    # údaje firmy
    name: str = Field(default="", max_length=120)
    street: str = Field(default="", max_length=120)
    city: str = Field(default="", max_length=80)
    postal_code: str = Field(default="", max_length=12)
    country: str = Field(default="Slovensko", max_length=60)
    ico: str = Field(default="", max_length=12)
    dic: str = Field(default="", max_length=14)
    ic_dph: str = Field(default="", max_length=16)
    email: str = Field(default="", max_length=120)
    phone: str = Field(default="", max_length=40)
    website: str = Field(default="", max_length=120)
    iban: str = Field(default="", max_length=42)

    # hlavička a pätička dokumentov (vzhľad určuje téma, nie firma)
    header_text: str = Field(default="", max_length=160)
    footer_text: str = Field(default="", max_length=300)
    show_page_numbers: bool = True

    @field_validator("ico")
    @classmethod
    def _ico_digits(cls, v: str) -> str:
        v = v.replace(" ", "")
        if v and not (v.isdigit() and 6 <= len(v) <= 8):
            raise ValueError("IČO má 6–8 číslic")
        return v

    @field_validator("iban")
    @classmethod
    def _iban_compact(cls, v: str) -> str:
        compact = v.replace(" ", "").upper()
        if compact and not (compact[:2].isalpha() and compact[2:].isalnum()):
            raise ValueError("IBAN musí začínať kódom krajiny")
        return compact

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        v = v.strip()
        if v and ("@" not in v or v.startswith("@") or v.endswith("@")):
            raise ValueError("neplatný e-mail")
        return v

    # spätná kompatibilita pre šablóny, ktoré ešte čítajú company.primary_color/accent_color
    # (workforce výpisy) — farby sú z témy, nie z nastavení
    @property
    def primary_color(self) -> str:
        return THEME.foreground

    @property
    def accent_color(self) -> str:
        return THEME.primary

    @property
    def iban_display(self) -> str:
        return " ".join(self.iban[i : i + 4] for i in range(0, len(self.iban), 4))

    @property
    def address_lines(self) -> list[str]:
        city_line = " ".join(p for p in (self.postal_code, self.city) if p)
        return [line for line in (self.street, city_line, self.country) if line]

    @property
    def registry_line(self) -> str:
        parts = [
            f"IČO: {self.ico}" if self.ico else "",
            f"DIČ: {self.dic}" if self.dic else "",
            f"IČ DPH: {self.ic_dph}" if self.ic_dph else "",
        ]
        return " · ".join(p for p in parts if p)


def load_company() -> CompanySettings:
    with get_conn() as conn:
        row = conn.execute("SELECT data_json FROM company_settings WHERE id = 1").fetchone()
    if row is None:
        return CompanySettings()
    # model_validate (nie construct) — staré uložené dáta prejdú cez defaulty nových polí
    return CompanySettings.model_validate(json.loads(row["data_json"]))


def save_company(data: CompanySettings) -> CompanySettings:
    payload = data.model_dump_json()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO company_settings (id, data_json) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET data_json = excluded.data_json,
                 updated_at = datetime('now')""",
            (payload,),
        )
    return data

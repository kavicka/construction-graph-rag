"""Téma PDF = téma aplikácie: tokeny sa nesmú rozísť s frontend/src/index.css."""

import re
from pathlib import Path

import pytest

from app.docgen.theme import FONTS_DIR, MONO_KEY, THEME

INDEX_CSS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "index.css"

# PdfTheme pole → CSS premenná v :root
TOKENS = {
    "foreground": "--foreground",
    "muted_foreground": "--muted-foreground",
    "muted": "--muted",
    "border": "--border",
    "input": "--input",
    "primary": "--primary",
    "chart_2": "--chart-2",
    "chart_3": "--chart-3",
    "destructive": "--destructive",
}


def _root_tokens() -> dict[str, str]:
    css = INDEX_CSS.read_text(encoding="utf-8")
    root = re.search(r":root\s*\{(.*?)\n\}", css, re.DOTALL)
    assert root, ":root blok nenájdený"
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;", root.group(1)))


@pytest.mark.skipif(not INDEX_CSS.exists(), reason="frontend nie je v kontajneri")
def test_theme_matches_frontend_css() -> None:
    css = _root_tokens()
    for field, var in TOKENS.items():
        assert getattr(THEME, field).lower() == css[var].lower(), f"{field} ≠ {var}"


@pytest.mark.skipif(not INDEX_CSS.exists(), reason="frontend nie je v kontajneri")
def test_font_stacks_match_frontend_css() -> None:
    css = INDEX_CSS.read_text(encoding="utf-8")
    assert "system-ui" in css and THEME.font_sans.startswith("system-ui")
    assert "JetBrains Mono" in css and "JetBrains Mono" in THEME.font_mono


def test_bundled_mono_font_files() -> None:
    for weight in (400, 700):
        for subset in ("latin", "ext"):
            assert (FONTS_DIR / f"{MONO_KEY}-{weight}-{subset}.woff2").stat().st_size > 1000
    css = THEME.font_face_css()
    assert css.count("@font-face") == 4 and "JetBrains Mono Ext" in css

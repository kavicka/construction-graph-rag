"""Vzhľad generovaných PDF = téma aplikácie (svetlá), nie nastavenie firmy.

Hodnoty zrkadlia `frontend/src/index.css` (`:root`): teplé neutrály, oranžový akcent,
plochý vzhľad bez tieňov. Font: `system-ui` pre text a JetBrains Mono pre štítky (dátumy,
čísla, metadáta) — rovnako ako `--font-sans` a `--font-mono` v appke. Test
`test_theme_matches_frontend` stráži, aby sa tokeny nerozišli s CSS.
"""

from dataclasses import dataclass
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
MONO_KEY = "jetbrains-mono"


@dataclass(frozen=True)
class PdfTheme:
    # mená tokenov = CSS premenné témy (--foreground, --primary, …)
    foreground: str = "#26251d"
    muted_foreground: str = "#8c8b85"
    background: str = "#ffffff"  # papier — nie krémové pozadie appky
    muted: str = "#efeee8"
    border: str = "#edece8"
    input: str = "#cfcdc4"
    primary: str = "#f54e00"
    chart_2: str = "#2f8f6a"
    chart_3: str = "#c98a12"
    destructive: str = "#cf2d56"
    font_sans: str = (
        'system-ui, -apple-system, "Segoe UI", "Helvetica Neue", Helvetica, Arial, '
        '"DejaVu Sans", sans-serif'
    )
    font_mono: str = '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace'

    def font_face_css(self) -> str:
        """@font-face pre JetBrains Mono — latin + latin-ext (č š ž ľ ť ň ď…)."""
        rules = []
        for weight in (400, 700):
            rules.append(
                f'@font-face {{ font-family: "JetBrains Mono"; font-weight: {weight}; '
                f'src: url("{MONO_KEY}-{weight}-latin.woff2"); }}'
            )
            rules.append(
                f'@font-face {{ font-family: "JetBrains Mono Ext"; font-weight: {weight}; '
                f'src: url("{MONO_KEY}-{weight}-ext.woff2"); }}'
            )
        return "\n".join(rules)

    @property
    def mono(self) -> str:
        """Mono stack s ext podmnožinou — renderer vyberá glyf po glyfe."""
        return f'"JetBrains Mono", "JetBrains Mono Ext", {self.font_mono.split(",", 1)[1].strip()}'


THEME = PdfTheme()

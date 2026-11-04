"""Look of generated PDFs = the app theme (light), not company settings.

Values mirror `frontend/src/index.css` (`:root`): warm neutrals, orange accent,
flat look without shadows. Font: `system-ui` for text and JetBrains Mono for labels (dates,
numbers, metadata) — same as `--font-sans` and `--font-mono` in the app. The test
`test_theme_matches_frontend` guards against the tokens drifting from the CSS.
"""

from dataclasses import dataclass
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
MONO_KEY = "jetbrains-mono"


@dataclass(frozen=True)
class PdfTheme:
    # token names = theme CSS variables (--foreground, --primary, …)
    foreground: str = "#26251d"
    muted_foreground: str = "#8c8b85"
    background: str = "#ffffff"  # paper — not the app's cream background
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
        """@font-face for JetBrains Mono — latin + latin-ext (č š ž ľ ť ň ď…)."""
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
        """Mono stack with the ext subset — the renderer picks glyph by glyph."""
        return f'"JetBrains Mono", "JetBrains Mono Ext", {self.font_mono.split(",", 1)[1].strip()}'


THEME = PdfTheme()

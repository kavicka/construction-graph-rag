"""HTML → PDF cez WeasyPrint.

Obsah dokumentu píše LLM, preto renderer smie načítať iba súbory z povolených
adresárov (fonty). Žiadne http(s), žiadne iné file:// cesty — inak by
`![](file:///etc/passwd)` alebo externý obrázok v markdowne prešiel do PDF.
"""

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app.docgen.theme import FONTS_DIR

HOMEBREW_PREFIXES = ("/opt/homebrew", "/usr/local")


class PdfEngineError(RuntimeError):
    pass


def _import_with_homebrew_fallback() -> Any:
    """macOS: WeasyPrint hľadá pango/gobject cez dlopen bez cesty, ktorý Homebrew
    knižnice v /opt/homebrew/lib nevidí (DYLD_FALLBACK_LIBRARY_PATH sa číta len pri
    štarte procesu). Počas importu preto dlopen skúsi aj absolútnu cestu."""
    import cffi

    prefixes = [os.environ.get("HOMEBREW_PREFIX", ""), *HOMEBREW_PREFIXES]
    lib_dirs = [Path(p) / "lib" for p in prefixes if p and (Path(p) / "lib").is_dir()]
    original = cffi.FFI.dlopen

    def dlopen(self: Any, name: Any, flags: int = 0) -> Any:
        try:
            return original(self, name, flags)
        except OSError:
            if isinstance(name, str) and "/" not in name:
                for d in lib_dirs:
                    if (d / name).exists():
                        return original(self, str(d / name), flags)
            raise

    cffi.FFI.dlopen = dlopen
    try:
        import weasyprint
    finally:
        cffi.FFI.dlopen = original
    return weasyprint


@lru_cache
def _weasyprint() -> Any:
    try:
        if sys.platform == "darwin":
            return _import_with_homebrew_fallback()
        import weasyprint

        return weasyprint
    except OSError as exc:
        raise PdfEngineError(
            "WeasyPrint nenašiel systémové knižnice (pango). macOS: `brew install pango`, "
            "Debian/Ubuntu: `apt install libpango-1.0-0 libpangoft2-1.0-0`."
        ) from exc


def pdf_engine_available() -> bool:
    try:
        _weasyprint()
    except PdfEngineError:
        return False
    return True


def _make_fetcher(allowed_dirs: list[Path]) -> Any:
    base: Any = _weasyprint().urls.URLFetcher
    allowed = [d.resolve() for d in allowed_dirs]

    class SandboxedFetcher(base):  # type: ignore[misc]
        def fetch(self, url: str, headers: Any = None) -> Any:
            parsed = urlparse(url)
            if parsed.scheme != "file":
                raise ValueError(f"blokovaná URL v dokumente: {url[:80]}")
            path = Path(unquote(parsed.path)).resolve()
            if not any(path.is_relative_to(d) for d in allowed):
                raise ValueError(f"blokovaný súbor v dokumente: {path.name}")
            return super().fetch(url, headers)

    return SandboxedFetcher()


@dataclass
class RenderedPdf:
    data: bytes
    pages: int


def render_pdf(html: str, *, allowed_dirs: list[Path]) -> RenderedPdf:
    weasyprint = _weasyprint()
    document = weasyprint.HTML(
        string=html,
        base_url=FONTS_DIR.as_uri() + "/",
        url_fetcher=_make_fetcher([FONTS_DIR, *allowed_dirs]),
    ).render()
    return RenderedPdf(data=document.write_pdf(), pages=len(document.pages))

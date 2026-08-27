"""Detekcia OCR enginu pri štarte.

Preferencia: ocrmac (Apple Vision — natívny, rýchly, takmer nulová RAM)
→ Tesseract so slk dátami → žiadne OCR (varovanie).
"""

import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class OcrInfo:
    engine: str  # "ocrmac" | "tesseract" | "none"
    languages: list[str]
    slovak: bool


def _vision_supported_languages() -> list[str] | None:
    try:
        from Vision import VNRecognizeTextRequest

        req = VNRecognizeTextRequest.alloc().init()
        langs, error = req.supportedRecognitionLanguagesAndReturnError_(None)
        if error is not None or langs is None:
            return None
        return [str(lang) for lang in langs]
    except Exception:
        return None


def _tesseract_languages() -> list[str] | None:
    if shutil.which("tesseract") is None:
        return None
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10
        )
        lines = [ln.strip() for ln in out.stdout.splitlines()[1:] if ln.strip()]
        return lines or None
    except (subprocess.SubprocessError, OSError):
        return None


@lru_cache
def detect_ocr() -> OcrInfo:
    try:
        import ocrmac  # noqa: F401

        vision_langs = _vision_supported_languages()
        if vision_langs is not None:
            slovak = any(lang.startswith("sk") for lang in vision_langs)
            langs = (["sk-SK"] if slovak else []) + ["en-US"]
            if slovak:
                return OcrInfo(engine="ocrmac", languages=langs, slovak=True)
            # Vision bez slovenčiny — skús tesseract so slk, inak ocrmac len EN
            tess = _tesseract_languages()
            if tess and "slk" in tess:
                return OcrInfo(engine="tesseract", languages=["slk", "eng"], slovak=True)
            return OcrInfo(engine="ocrmac", languages=["en-US"], slovak=False)
    except ImportError:
        pass

    tess = _tesseract_languages()
    if tess:
        slovak = "slk" in tess
        langs = (["slk"] if slovak else []) + (["eng"] if "eng" in tess else [])
        return OcrInfo(engine="tesseract", languages=langs or tess[:1], slovak=slovak)

    return OcrInfo(engine="none", languages=[], slovak=False)

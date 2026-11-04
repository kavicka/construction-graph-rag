"""Supported document formats — detected by content, not just extension.

Docling parses everything (same chunking, tables and graph). Only PDF has the coordinates for
citation highlighting and a page viewer; other formats show the cited passage as text.
"""

import codecs
import io
import zipfile
from pathlib import Path

# format → (media type, directory that must be in the ZIP package of the Office format)
FORMATS: dict[str, tuple[str, str | None]] = {
    "pdf": ("application/pdf", None),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word/"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "ppt/"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xl/"),
    "html": ("text/html", None),
    "md": ("text/markdown", None),
}
EXTENSION_ALIASES = {"htm": "html", "markdown": "md"}
ACCEPT = ",".join(f".{f}" for f in [*FORMATS, *EXTENSION_ALIASES])


def media_type(fmt: str) -> str:
    return FORMATS.get(fmt, ("application/octet-stream", None))[0]


def detect_format(filename: str, data: bytes) -> str | None:
    """→ format key or None. PDF is recognised by its header even with a different extension."""
    if data.startswith(b"%PDF"):
        return "pdf"
    ext = Path(filename).suffix.casefold().lstrip(".")
    ext = EXTENSION_ALIASES.get(ext, ext)
    if ext not in FORMATS or ext == "pdf":
        return None
    package_dir = FORMATS[ext][1]
    if package_dir is not None:
        if not data.startswith(b"PK\x03\x04"):
            return None
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if not any(n.startswith(package_dir) for n in zf.namelist()):
                    return None
        except zipfile.BadZipFile:
            return None
        return ext
    try:
        # incremental decoder — a cut at 64 kB can split a multi-byte character (č, ž…)
        codecs.getincrementaldecoder("utf-8")().decode(data[:65536], final=False)
    except UnicodeDecodeError:
        return None
    return ext

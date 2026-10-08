"""Podporované formáty dokumentov — rozpoznanie podľa obsahu, nie len prípony.

Všetko parsuje Docling (rovnaký chunking, tabuľky aj graf). Súradnice na zvýraznenie
citácie a prehliadač strán má len PDF; ostatné formáty ukazujú citovanú pasáž ako text.
"""

import codecs
import io
import zipfile
from pathlib import Path

# formát → (media type, adresár, ktorý musí byť v ZIP balíku Office formátu)
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
    """→ kľúč formátu alebo None. PDF sa pozná podľa hlavičky aj s inou príponou."""
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
        # inkrementálny dekodér — rez na 64 kB môže pretnúť viacbajtový znak (č, ž…)
        codecs.getincrementaldecoder("utf-8")().decode(data[:65536], final=False)
    except UnicodeDecodeError:
        return None
    return ext

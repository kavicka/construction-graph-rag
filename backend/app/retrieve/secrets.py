"""Redakcia tajomstiev — heslá, PIN-y, prístupové kódy, kľúče a tokeny sa nikdy nevypíšu.

Dve vrstvy:
1. vzory, ktoré sú tajomstvom samy osebe (hash hesla, API token, privátny kľúč, JWT…),
2. návestie + hodnota („Heslo k WiFi: Kral-2026“, „PIN 4821“, „api_key=…“).

Redakcia beží na texte zdrojov PRED promptom (LLM tajomstvo nikdy nevidí), na priebežnom
streame odpovede, na finálnej odpovedi a na všetkom, čo chat, MCP a vyhľadávanie vracajú.
IBAN, rodné čísla a telefóny sa NEredigujú — v zmluvách sú bežné (rozhodnutie z /ask).
"""

import re

MASK = "••••••"

# --- tajomstvá podľa tvaru ---------------------------------------------------------------

_SHAPES = [
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
    r"\bscrypt\$[0-9a-f]+\$[0-9a-f]+",
    r"\$2[abxy]?\$\d{2}\$[./A-Za-z0-9]{53}",
    r"\$argon2(?:id|i|d)\$[^\s]+",
    r"\brag_[A-Za-z0-9_\-]{20,}",
    r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bxox[abprs]-[A-Za-z0-9\-]{10,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"\bAIza[0-9A-Za-z_\-]{35}\b",
    r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
    r"(?<=\bBearer )[A-Za-z0-9._\-~+/]{20,}=*",
    r"\b[0-9a-fA-F]{32,}\b",
]
_SHAPE_RE = re.compile("|".join(f"(?:{p})" for p in _SHAPES))

# --- návestie + hodnota ------------------------------------------------------------------

_LABELS = (
    r"hesl[oáa]\w*|password\w*|passwd|passphrase|pwd|pin(?:[ \-]?k[óo]d\w*)?|"
    r"pr[íi]stupov\w*\s+k[óo]d\w*|k[óo]d\w*\s+(?:od\s+|k\s+|na\s+|do\s+)?"
    r"(?:br[áa]n\w*|alarm\w*|dver[íi]\w*|trezor\w*|z[áa]mk\w*|vchod\w*|wi-?fi)|"
    r"api[ _\-]?(?:k[ľl][úu][čc]\w*|key|token)|secret(?:[ _\-]?key)?|"
    r"(?:tajn|s[úu]kromn|priv[áa]tn|licen[čc]n)\w*\s+k[ľl][úu][čc]\w*|private[ _\-]key|"
    r"access[ _\-]?(?:token|key|code)|token|wpa2?(?:[ _\-]?k[ľl][úu][čc]\w*)?"
)
_LABEL_RE = re.compile(rf"(?<![\w-])(?:{_LABELS})(?![\w-])", re.IGNORECASE)
# „Heslo k WiFi na stavbe: Kral-2026“ — medzi návestím a oddeľovačom max. 4 slová
_EXPLICIT_RE = re.compile(
    rf"(?P<label>(?<![\w-])(?:{_LABELS})(?![\w-]))"
    r"(?P<mid>(?:[ \t]+[^\s:=*()\[\]]{1,20}){0,4}?[ \t]*(?:[:=]|\s[–-]\s)[ \t]*\n?[ \t]*)"
    r"(?P<val>[\"'„“]?[^\s\"'„“”]{3,}[\"'“”]?)",
    re.IGNORECASE,
)
# „heslo je Kral2026!“, „the password is …“ — hodnota sa začierni, len ak vyzerá ako tajomstvo
_VERB_RE = re.compile(
    rf"(?P<label>(?<![\w-])(?:{_LABELS})(?![\w-]))"
    r"(?P<mid>(?:[ \t]+[^\s:=*()\[\]]{1,20}){0,3}?[ \t]+(?:je|bude|znie|is|was|equals)[ \t]+)"
    r"(?P<val>[\"'„“]?[^\s\"'„“”]{3,}[\"'“”]?)",
    re.IGNORECASE,
)
# „PIN 4821“, „PIN: 4821“
_PIN_RE = re.compile(r"(?<![\w-])(?P<label>pin\w{0,3})(?P<mid>[ \t:=]+)(?P<val>\d{4,8})\b",
                     re.IGNORECASE)
_TRAIL = ".,;:!?)]}…"


def _looks_secret(value: str) -> bool:
    core = value.strip("\"'„“”" + _TRAIL)
    if len(core) < 3 or core == MASK:
        return False
    return bool(
        re.search(r"\d", core) or re.search(r"[^\w]", core) or re.search(r"\w[A-Z]", core)
    )


def _mask_value(m: re.Match[str], *, strict: bool) -> str:
    value = m.group("val")
    if value.strip("\"'„“”") == MASK or (strict and not _looks_secret(value)):
        return m.group(0)
    core = value.rstrip(_TRAIL)
    tail = value[len(core):]
    return f"{m.group('label')}{m.group('mid')}{MASK}{tail}"


def redact_secrets(text: str) -> str:
    """Text bez tajomstiev. Idempotentné — zamaskovaný text sa druhým prechodom nezmení."""
    if not text:
        return text
    text = _SHAPE_RE.sub(MASK, text)
    text = _PIN_RE.sub(lambda m: _mask_value(m, strict=False), text)
    text = _EXPLICIT_RE.sub(lambda m: _mask_value(m, strict=False), text)
    return _VERB_RE.sub(lambda m: _mask_value(m, strict=True), text)


def contains_secret(text: str) -> bool:
    return redact_secrets(text) != text


class SecretStreamFilter:
    """Priebežná redakcia streamu tokenov.

    Vypúšťa text len po poslednú medzeru (celé slová → tvarové tajomstvá sa nerozseknú)
    a chvost od posledného návestia („heslo“, „PIN“…) drží, kým nepríde hodnota — inak by
    „Heslo: “ odišlo v jednom kúsku a „Kral-2026“ v ďalšom, mimo dosahu redakcie."""

    HOLD_CHARS = 90  # návestie + oddeľovač + hodnota sa zmestia do tohto okna

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, token: str) -> str:
        self._buf += token
        cut = max(self._buf.rfind(" "), self._buf.rfind("\n"))
        if cut < 0:
            return ""
        last_label = None
        for m in _LABEL_RE.finditer(self._buf):
            last_label = m
        if last_label is not None and len(self._buf) - last_label.start() <= self.HOLD_CHARS:
            cut = min(cut, last_label.start() - 1)
        if cut < 0:
            return ""
        head, self._buf = self._buf[: cut + 1], self._buf[cut + 1:]
        return redact_secrets(head)

    def flush(self) -> str:
        out, self._buf = redact_secrets(self._buf), ""
        return out

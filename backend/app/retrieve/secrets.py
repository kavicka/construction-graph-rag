"""Secret redaction — passwords, PINs, access codes, keys and tokens are never printed.

Two layers:
1. patterns that are secrets by themselves (password hash, API token, private key, JWT…),
2. label + value („Heslo k WiFi: Kral-2026“, „PIN 4821“, „api_key=…“).

Redaction runs on source text BEFORE the prompt (the LLM never sees the secret), on the live
answer stream, on the final answer and on everything chat, MCP and search return.
IBANs, national ID numbers and phones are NOT redacted — common in contracts (decision from /ask).
"""

import re

MASK = "••••••"

# --- secrets by shape ---------------------------------------------------------------

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

# --- label + value ------------------------------------------------------------------

_LABELS = (
    r"hesl[oáa]\w*|password\w*|passwd|passphrase|pwd|pin(?:[ \-]?k[óo]d\w*)?|"
    r"pr[íi]stupov\w*\s+k[óo]d\w*|k[óo]d\w*\s+(?:od\s+|k\s+|na\s+|do\s+)?"
    r"(?:br[áa]n\w*|alarm\w*|dver[íi]\w*|trezor\w*|z[áa]mk\w*|vchod\w*|wi-?fi)|"
    r"api[ _\-]?(?:k[ľl][úu][čc]\w*|key|token)|secret(?:[ _\-]?key)?|"
    r"(?:tajn|s[úu]kromn|priv[áa]tn|licen[čc]n)\w*\s+k[ľl][úu][čc]\w*|private[ _\-]key|"
    r"access[ _\-]?(?:token|key|code)|token|wpa2?(?:[ _\-]?k[ľl][úu][čc]\w*)?"
)
_LABEL_RE = re.compile(rf"(?<![\w-])(?:{_LABELS})(?![\w-])", re.IGNORECASE)
# „Heslo k WiFi na stavbe: Kral-2026“ — at most 4 words between the label and the separator
_EXPLICIT_RE = re.compile(
    rf"(?P<label>(?<![\w-])(?:{_LABELS})(?![\w-]))"
    r"(?P<mid>(?:[ \t]+[^\s:=*()\[\]]{1,20}){0,4}?[ \t]*(?:[:=]|\s[–-]\s)[ \t]*\n?[ \t]*)"
    r"(?P<val>[\"'„“]?[^\s\"'„“”]{3,}[\"'“”]?)",
    re.IGNORECASE,
)
# „heslo je Kral2026!“, „the password is …“ — the value is blacked out only if it looks like
# a secret
_VERB_RE = re.compile(
    rf"(?P<label>(?<![\w-])(?:{_LABELS})(?![\w-]))"
    r"(?P<mid>(?:[ \t]+[^\s:=*()\[\]]{1,20}){0,3}?[ \t]+(?:je|bude|znie|is|was|equals)[ \t]+)"
    r"(?P<val>[\"'„“]?[^\s\"'„“”]{3,}[\"'“”]?)",
    re.IGNORECASE,
)
# „PIN 4821“, „PIN: 4821“
_PIN_RE = re.compile(
    r"(?<![\w-])(?P<label>pin\w{0,3})(?P<mid>[ \t:=]+)(?P<val>\d{4,8})\b", re.IGNORECASE
)
_TRAIL = ".,;:!?)]}…"


def _looks_secret(value: str) -> bool:
    core = value.strip("\"'„“”" + _TRAIL)
    if len(core) < 3 or core == MASK:
        return False
    return bool(re.search(r"\d", core) or re.search(r"[^\w]", core) or re.search(r"\w[A-Z]", core))


def _mask_value(m: re.Match[str], *, strict: bool) -> str:
    value = m.group("val")
    if value.strip("\"'„“”") == MASK or (strict and not _looks_secret(value)):
        return m.group(0)
    core = value.rstrip(_TRAIL)
    tail = value[len(core) :]
    return f"{m.group('label')}{m.group('mid')}{MASK}{tail}"


def redact_secrets(text: str) -> str:
    """Text without secrets. Idempotent — masked text does not change on a second pass."""
    if not text:
        return text
    text = _SHAPE_RE.sub(MASK, text)
    text = _PIN_RE.sub(lambda m: _mask_value(m, strict=False), text)
    text = _EXPLICIT_RE.sub(lambda m: _mask_value(m, strict=False), text)
    return _VERB_RE.sub(lambda m: _mask_value(m, strict=True), text)


def contains_secret(text: str) -> bool:
    return redact_secrets(text) != text


class SecretStreamFilter:
    """Live redaction of the token stream.

    Emits text only up to the last space (whole words → shape-based secrets are not split)
    and holds the tail from the last label („heslo“, „PIN“…) until the value arrives — otherwise
    „Heslo: “ would go out in one piece and „Kral-2026“ in the next, beyond redaction's reach."""

    HOLD_CHARS = 90  # label + separator + value fit into this window

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
        head, self._buf = self._buf[: cut + 1], self._buf[cut + 1 :]
        return redact_secrets(head)

    def flush(self) -> str:
        out, self._buf = redact_secrets(self._buf), ""
        return out

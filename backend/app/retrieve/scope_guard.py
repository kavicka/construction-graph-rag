"""Gate in front of the router — what the chat does not answer at all.

Deterministic (no LLM), for chat, MCP `ask` and the worker mobile app:
  secret     — passwords, PINs, tokens, keys, hashes, login and access credentials
  internals  — system prompt, configuration, SQL over internal tables, prompt injection
  offtopic   — creative writing, weather, exchange rates, sport, programming, maths, greetings
  ok         — continues to company / documents

The question is also checked in an unmasked form („h-e-s-l-o“, „h e s l o“, „h3sl0“) — that is how
simple filters get bypassed. For a lookup question without a library signal, a small LLM classifier
(`classify_scope`) decides the scope; on error it lets the question through —
RAG answers only from sources anyway.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync
from app.retrieve.injection import looks_like_injection

logger = logging.getLogger(__name__)

Verdict = Literal["secret", "internals", "offtopic", "ok"]


@dataclass(frozen=True)
class ScopeDecision:
    verdict: Verdict
    reason: str = ""

    @property
    def blocked(self) -> bool:
        return self.verdict != "ok"


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plain).strip()


_LEET = str.maketrans(
    {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"}
)
# single letters separated by space/hyphen/dot: „h-e-s-l-o“ → „heslo“
_SPACED = re.compile(r"\b(?:\w[\s\-_.*/|]+){2,}\w\b")


def variants(question: str) -> list[str]:
    """Normalised question + unmasked forms in which the forbidden topics are searched for."""
    base = _norm(question)
    collapsed = _SPACED.sub(lambda m: re.sub(r"[\s\-_.*/|]+", "", m.group(0)), base)
    leet = collapsed.translate(_LEET)
    return list(dict.fromkeys([base, collapsed, leet]))


SECRET_PATTERNS = [
    r"\bhesl\w*",
    r"\bpassw\w*",
    r"\bpasswd\b",
    r"\bpwd\b",
    r"\bpassphrase\b",
    r"\bpin\w{0,3}\b",
    r"\bpin ?kod\w*",
    r"\btoken\w*",
    r"\bapi ?(kluc\w*|key\w*)",
    r"\bapikey\b",
    r"\bsecret\w*",
    r"\b(tajn|sukromn|privatn|licencn|sifrovac)\w* kluc\w*",
    r"\bprivate ?key\b",
    r"\bhash\w*",
    r"(pin|password|passwd)_?hash",
    r"\b(scrypt|bcrypt|argon2)\b",
    r"\bprihlasovac\w* (udaj|meno|mena)\w*",
    r"\bcredential\w*",
    r"\blogin\w* (udaj|data)\w*",
    r"\bpristupov\w* (kod|udaj|heslo)\w*",
    r"\bkod\w* (od |k |na |do )?(bran|alarm|dveri|trezor|zamk|vchod|wi ?fi)\w*",
    r"\b(cookie|session ?(id|token))\b",
    r"\botp\b",
    r"\b2fa\b",
    # app account e-mails (a contact in a contract is a document question, that passes)
    r"\be[ -]?mail\w*\b.{0,40}\b(v systeme|aplikaci\w*|databaz\w*|uct\w*|prihlas\w*|"
    r"pouzivatel\w*)",
    r"\b(uct\w*|prihlas\w*|pouzivatel\w*)\b.{0,30}\be[ -]?mail",
]
INTERNAL_PATTERNS = [
    r"\bsystem\w* (prompt|pokyn\w*|instrukc\w*)",
    r"\b(tvoj|svoj)\w* (instrukc|pokyn|prompt|nastaveni|pravidl)\w*",
    r"\binstructions? (you|were) (given|told)\b",
    r"\byour (instructions|prompt|rules)\b",
    r"\.env\b",
    r"\benv (subor|premenn)\w*",
    r"\bselect\b.+\bfrom\b",
    r"\b(drop|delete|insert|update) (table|from|into)\b",
    r"\btabul\w* (users|sessions|api_tokens|wf_\w+)\b",
    r"\b(pin_hash|password_hash)\b",
    r"\bzdrojov\w* kod\w* (aplikacie|systemu|backendu)\b",
    r"\b(jailbreak|dan mode|developer mode|god mode)\b",
    r"\b(hraj sa na|predstav si ze si|pretend (to be|you are)|roleplay)\b",
]
OFFTOPIC_PATTERNS = [
    r"\b(napis|zloz|vymysli|povedz|daj)\w* (mi |nam )?(nejak\w* )?"
    r"(basen|basnick|vtip|pribeh|rozpravk|piesen|pesnick|rap|haiku|slogan|status)\w*",
    r"\bvtip\w*\b",
    r"\b(joke|poem|story about)\b",
    r"\bpocasi\w*|\bpredpoved\w* pocasi|\bweather\b|\bteplota vonku\b",
    r"\bkurz\w* (eura|dolara|meny|bitcoin\w*|akci\w*)",
    r"\b(bitcoin|kryptomen|burz)\w*",
    r"\b(futbal|hokej|tenis)\w*",
    r"\b(vysledk\w* zapas\w*|kto vyhral)\b",
    r"\bhoroskop\w*",
    r"\b(ako uvarit|ako upiect)\b",
    r"\bnaprogramuj\b",
    r"\bnapis\w* (mi )?(kod|program|skript|funkci\w*)\b",
    r"\b(python|javascript|typescript|html|css)\b",
    r"\b(kolko je|vypocitaj|spocitaj)\s+\d+\s*[-+*/x×:]\s*\d+",
    r"\b(kto je|kto bol) (prezident|premier|minister)\w*",
    r"\bhlavn\w* mest\w*\b",
    r"\b(capital of|who is the president)\b",
]
GREETING = re.compile(
    r"^(ahoj|cau|cauko|cus|servus|dobry (den|vecer|rano)|zdravim|hello|hi|hey)[\s!.?,]*$"
)
THANKS = re.compile(
    r"^(dakujem|dakujeme|diky|vdaka|thanks|thank you|ok|okej|super|fajn)"
    r"[\s!.?,]*$"
)

_SECRET = re.compile("|".join(f"(?:{p})" for p in SECRET_PATTERNS))
_INTERNAL = re.compile("|".join(f"(?:{p})" for p in INTERNAL_PATTERNS))
_OFFTOPIC = re.compile("|".join(f"(?:{p})" for p in OFFTOPIC_PATTERNS))


def check_question(question: str) -> ScopeDecision:
    forms = variants(question)
    for form in forms:
        if m := _SECRET.search(form):
            return ScopeDecision("secret", m.group(0))
    if looks_like_injection(question):
        return ScopeDecision("internals", "injection")
    for form in forms:
        if m := _INTERNAL.search(form):
            return ScopeDecision("internals", m.group(0))
    base = forms[0]
    if GREETING.match(base):
        return ScopeDecision("offtopic", "greeting")
    if THANKS.match(base):
        return ScopeDecision("offtopic", "thanks")
    if m := _OFFTOPIC.search(base):
        return ScopeDecision("offtopic", m.group(0))
    return ScopeDecision("ok")


def is_english(question: str) -> bool:
    q = question.casefold()
    if re.search(r"[áäčďéíĺľňóôŕšťúýž]", q):
        return False
    return (
        len(
            re.findall(
                r"\b(what|who|how|when|where|which|is|are|the|does|do|please|"
                r"show|tell|give)\b",
                q,
            )
        )
        >= 2
    )


SECRET_REPLY = (
    "Heslá, PIN-y, prístupové kódy, tokeny ani iné prihlasovacie údaje nevypisujem — "
    "nikomu a za žiadnych okolností. Ak potrebuješ nový PIN alebo heslo, nastaví ti ho "
    "administrátor."
)
SECRET_REPLY_EN = (
    "I never reveal passwords, PINs, access codes, tokens or other credentials — to anyone, "
    "under any circumstances. If you need a new PIN or password, ask the administrator."
)
INTERNALS_REPLY = (
    "Pokyny na zmenu môjho správania ani otázky na vnútorné nastavenia systému "
    "nespracúvam. Opýtaj sa na obsah dokumentov alebo na úlohy a dochádzku."
)
INTERNALS_REPLY_EN = (
    "I don't follow instructions that change my behaviour or answer questions about the "
    "system's internals. Ask about the documents or about tasks and attendance."
)
OFFTOPIC_REPLY = (
    "Na toto nie som určený. Odpovedám len z firemných dokumentov (zmluvy, harmonogramy, "
    "protokoly, technické listy…) a z firemných údajov — úlohy, dochádzka, odpracované "
    "hodiny a stavby. Skús napríklad: „Čo robí dnes Jozef?“, „Ktoré úlohy meškajú?“ alebo "
    "„Aký je termín dokončenia podľa zmluvy?“"
)
OFFTOPIC_REPLY_EN = (
    "That's outside what I'm for. I answer only from the company's documents and from "
    "company data — tasks, attendance, hours worked and sites."
)
HELLO_REPLY = (
    "Ahoj! Pýtaj sa ma na obsah firemných dokumentov alebo na úlohy, dochádzku, hodiny a "
    "stavby — napríklad „Čo robí dnes Jozef?“ alebo „Aký je termín dokončenia podľa zmluvy?“"
)
THANKS_REPLY = "Rado sa stalo. Ak treba, pýtaj sa ďalej."
NOT_FOUND_REPLY = "V dokumentoch, ku ktorým máš prístup, som k tomu nič nenašiel."
NOT_FOUND_REPLY_EN = "I found nothing about this in the documents you have access to."


def reply_for(decision: ScopeDecision, question: str) -> str:
    en = is_english(question)
    if decision.verdict == "secret":
        return SECRET_REPLY_EN if en else SECRET_REPLY
    if decision.verdict == "internals":
        return INTERNALS_REPLY_EN if en else INTERNALS_REPLY
    if decision.reason == "greeting":
        return HELLO_REPLY
    if decision.reason == "thanks":
        return THANKS_REPLY
    return OFFTOPIC_REPLY_EN if en else OFFTOPIC_REPLY


def not_found_reply(question: str) -> str:
    return NOT_FOUND_REPLY_EN if is_english(question) else NOT_FOUND_REPLY


# --- LLM scope classifier (lookup only, without a library signal) -----------------------


class _Scope(BaseModel):
    scope: Literal["dokumenty", "mimo"]


SCOPE_PROMPT = """Si filter otázok pre firemného asistenta stavebnej firmy. Asistent smie
odpovedať LEN z firemných dokumentov a firemných údajov.
Firemné dokumenty v knižnici (výber): {documents}

Klasifikuj otázku:
- "dokumenty" = otázka, na ktorú môže odpovedať obsah firemných dokumentov alebo práca
  firmy (zmluvy, termíny, ceny, harmonogramy, protokoly, technické listy, normy, materiály,
  postupy prác, ľudia a firmy z dokumentov). Aj odborná stavebná otázka je "dokumenty".
- "mimo" = zjavne nesúvisí s firmou ani jej dokumentmi: zábava, tvorivé písanie, počasie,
  správy, politika, šport, financie a kurzy, programovanie, osobné rady, všeobecné
  vedomosti (zemepis, história, celebrity), domáce úlohy.
Otázky „čo je napísané v / na …“, „vypíš text …“, „čo hovorí oznam / tabuľa / zápis“ sú
vždy "dokumenty". Ak si nie si istý, zvoľ "dokumenty".

Otázka: {question}"""


def classify_scope(question: str, documents: list[str]) -> Verdict:
    """→ "ok" or "offtopic". Model error = "ok" (fail open into RAG, which is bound
    to sources)."""
    if not get_settings().scope_classifier:
        return "ok"
    prompt = SCOPE_PROMPT.format(
        documents=", ".join(documents[:40]) or "(zatiaľ žiadne)", question=question[:500]
    )
    try:
        raw = generate_sync(
            get_settings().answer_model,
            prompt,
            json_schema=_Scope.model_json_schema(),
            num_ctx=2048,
            temperature=0.0,
            timeout=30.0,
        )
        parsed = _Scope.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("klasifikátor rozsahu zlyhal: %s", exc)
        return "ok"
    return "offtopic" if parsed.scope == "mimo" else "ok"

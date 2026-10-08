"""Pamäť konverzácie — doplňujúca otázka sa prepíše na samostatnú.

„A aká je tam pokuta?“ po otázke o zmluve Agátová → „Aká je zmluvná pokuta v zmluve
o dielo Agátová?“. Retrieval aj odpoveď potom pracujú so samostatnou otázkou; história
do promptu odpovede nejde (malé kontextové okno patrí zdrojom).

LLM sa volá len pri náznaku nadväznosti (zámená, krátka otázka) — samostatné otázky
prechádzajú bez latencie navyše.
"""

import logging
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync

logger = logging.getLogger(__name__)

HISTORY_TURNS = 3  # posledné 3 výmeny otázka–odpoveď
ANSWER_SNIPPET = 400

FOLLOW_UP = re.compile(
    r"\b(to|tom|toho|tomu|ten|tej|tu|tá|tie|tých|jeho|jej|ich|tam|tiež|aj|ešte|a čo|"
    r"a kde|a kedy|a koľko|a kto|rovnak|druh[ýáé]|predchádzaj|"
    r"it|its|that|this|those|they|them|there|also|what about|and the|same|previous)\b",
    re.IGNORECASE,
)
SHORT_QUESTION_WORDS = 5


class _Rewrite(BaseModel):
    # najprv pomenovať, na čo sa otázka odkazuje — bez tohto kroku malý model otázku
    # len zopakoval (eval: 5/5 doplňujúcich otázok bez zmeny)
    refers_to: str = Field(max_length=200)
    question: str = Field(min_length=3, max_length=500)


REWRITE_PROMPT = """Úloha: prepíš POSLEDNÚ otázku používateľa na samostatnú otázku, ktorej
rozumie aj niekto, kto históriu nevidel. Do `refers_to` napíš, na čo sa otázka odkazuje
(vzorka, projekt, dokument, osoba, vec z histórie). Do `question` napíš celú otázku s
doplneným odkazom. Zachovaj jazyk poslednej otázky. Na otázku neodpovedaj.

Príklad 1
História:
Používateľ: Aký prietok mala vzorka A-02?
Asistent: Vzorka A-02 mala prietok 13,1 l/min.
Posledná otázka: A akú mala teplotu?
→ {{"refers_to": "vzorka A-02", "question": "Akú teplotu mala vzorka A-02?"}}

Príklad 2
História:
Používateľ: What is the contract price of the Agatova project?
Asistent: The price is 4 860 000 EUR.
Posledná otázka: And the deadline?
→ {{"refers_to": "Agatova contract", "question": "What is the Agatova completion deadline?"}}

Teraz:
História:
{history}
Posledná otázka: {question}
→"""


def needs_rewrite(question: str, history: list[dict[str, Any]]) -> bool:
    if not any(m["role"] == "user" for m in history):
        return False
    return bool(FOLLOW_UP.search(question)) or len(question.split()) <= SHORT_QUESTION_WORDS


def _history_text(history: list[dict[str, Any]]) -> str:
    turns: list[str] = []
    for m in history[-HISTORY_TURNS * 2 :]:
        if m["role"] == "user":
            turns.append(f"Používateľ: {m['content']}")
        else:
            turns.append(f"Asistent: {m['content'][:ANSWER_SNIPPET]}")
    return "\n".join(turns)


def _last_user_question(history: list[dict[str, Any]]) -> str | None:
    return next((m["content"] for m in reversed(history) if m["role"] == "user"), None)


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _changed(original: str, rewritten: str) -> bool:
    """Prepis sa počíta, len ak doplnil nové slovo (odkaz z histórie) — vypustenie
    zámena („ten snímač“ → „snímač“) informáciu nepridá."""
    return bool(_words(rewritten) - _words(original))


def rewrite_question(question: str, history: list[dict[str, Any]]) -> str:
    """LLM prepis; ak model otázku nezmení, spojí sa s predchádzajúcou otázkou
    („Aký prietok mala A-02? A akú mala teplotu?“) — tomu rozumie retrieval aj LLM."""
    prompt = REWRITE_PROMPT.format(history=_history_text(history), question=question)
    rewritten = question
    try:
        raw = generate_sync(
            get_settings().answer_model, prompt,
            json_schema=_Rewrite.model_json_schema(), temperature=0.0, num_ctx=4096,
            timeout=60,
        )
        candidate = _Rewrite.model_validate_json(raw).question.strip()
        # model občas „odpovie“ namiesto prepisu — prepis musí zostať otázkou rozumnej dĺžky
        if len(candidate) <= max(300, len(question) * 6):
            rewritten = candidate
    except (OllamaError, ValidationError) as exc:
        logger.warning("prepis otázky zlyhal: %s", exc)
    if _changed(question, rewritten):
        return rewritten
    previous = _last_user_question(history)
    return f"{previous} {question}" if previous else question

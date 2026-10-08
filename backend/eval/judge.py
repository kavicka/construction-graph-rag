"""LLM-as-judge pre faithfulness: rozloží odpoveď na tvrdenia a overí ich voči zdrojom.

Sudca je ten istý lokálny model ako generátor (qwen3:4b) — self-preference bias je
známe obmedzenie; čísla preto slúžia na porovnanie konfigurácií medzi sebou, nie ako
absolútna pravda. Presné čísla kontroluje deterministicky `contains_expected`.
"""

import logging

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.llm.ollama_client import OllamaError, generate_sync

logger = logging.getLogger(__name__)


class _Claim(BaseModel):
    claim: str
    supported: bool


class _Verdict(BaseModel):
    claims: list[_Claim] = Field(default_factory=list, max_length=20)


JUDGE_PROMPT = """Si prísny hodnotiteľ odpovedí RAG systému.
Rozlož ODPOVEĎ na jednotlivé faktické tvrdenia (bez citácií [n]). Pri každom urč, či ho
ZDROJE priamo podporujú (supported=true), alebo nie (false). Tvrdenie, že informácia
v zdrojoch chýba, je supported=true, ak ju zdroje naozaj neobsahujú.

ZDROJE:
{sources}

ODPOVEĎ:
{answer}"""


def judge_faithfulness(answer: str, sources_text: str) -> float | None:
    """Podiel podporených tvrdení; None ak sudca zlyhal alebo odpoveď nemá tvrdenia."""
    try:
        raw = generate_sync(
            get_settings().answer_model,
            JUDGE_PROMPT.format(sources=sources_text[:12000], answer=answer[:3000]),
            json_schema=_Verdict.model_json_schema(), temperature=0.0, num_ctx=8192,
            timeout=180,
        )
        verdict = _Verdict.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("judge zlyhal: %s", exc)
        return None
    if not verdict.claims:
        return None
    return sum(c.supported for c in verdict.claims) / len(verdict.claims)

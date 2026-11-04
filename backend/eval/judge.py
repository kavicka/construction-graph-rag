"""LLM-as-judge for faithfulness: splits the answer into claims and checks them against the sources.

The judge is the same local model as the generator (qwen3:4b) — self-preference bias is a
known limitation; the numbers therefore serve to compare configurations with each other, not as
absolute truth. Exact numbers are checked deterministically by `contains_expected`.
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
    """Share of supported claims; None if the judge failed or the answer has no claims."""
    try:
        raw = generate_sync(
            get_settings().answer_model,
            JUDGE_PROMPT.format(sources=sources_text[:12000], answer=answer[:3000]),
            json_schema=_Verdict.model_json_schema(),
            temperature=0.0,
            num_ctx=8192,
            timeout=180,
        )
        verdict = _Verdict.model_validate_json(raw)
    except (OllamaError, ValidationError) as exc:
        logger.warning("judge zlyhal: %s", exc)
        return None
    if not verdict.claims:
        return None
    return sum(c.supported for c in verdict.claims) / len(verdict.claims)

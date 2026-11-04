import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import detect_ram_gb, get_settings
from app.docgen.pdf import pdf_engine_available
from app.ocr import detect_ocr

router = APIRouter()


class OllamaStatus(BaseModel):
    up: bool
    models: dict[str, bool]


class OcrStatus(BaseModel):
    engine: str
    languages: list[str]
    slovak: bool


class HealthResponse(BaseModel):
    status: str
    version: str
    ram_gb: int
    ram_profile: str
    embedding_model: str
    ollama: OllamaStatus
    ocr: OcrStatus
    pdf_engine: bool  # WeasyPrint + pango — required for document generation


async def check_ollama(base_url: str, wanted: list[str]) -> OllamaStatus:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{base_url}/api/tags")
            resp.raise_for_status()
            available = {m["name"] for m in resp.json().get("models", [])}
    except (httpx.HTTPError, KeyError, ValueError):
        return OllamaStatus(up=False, models=dict.fromkeys(wanted, False))
    # ollama tags look like "qwen3:4b" — compared directly on name
    return OllamaStatus(up=True, models={m: m in available for m in wanted})


@router.get("/health")
async def health() -> HealthResponse:
    s = get_settings()
    ollama = await check_ollama(s.ollama_base_url, [s.answer_model, s.extract_model])
    ocr = detect_ocr()
    return HealthResponse(
        status="ok",
        version="0.1.0",
        ram_gb=detect_ram_gb(),
        ram_profile=s.effective_profile,
        embedding_model=s.effective_embedding_model,
        ollama=ollama,
        ocr=OcrStatus(engine=ocr.engine, languages=ocr.languages, slovak=ocr.slovak),
        pdf_engine=pdf_engine_available(),
    )

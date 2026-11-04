import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

RamProfile = Literal["auto", "low", "high"]

# High profil zapína ťažšie modely (bge-m3, reranker); prah zvolený tak,
# aby 16 GB stroje ostali na low profile — tam je bge-m3 + LLM naraz tesný.
HIGH_PROFILE_MIN_GB = 32

PROFILE_EMBEDDING_MODELS: dict[str, str] = {
    "low": "intfloat/multilingual-e5-small",
    "high": "BAAI/bge-m3",
}
PROFILE_RERANKER_MODELS: dict[str, str] = {
    # mMiniLMv2 (Apache-2.0, ~120M) — multilingválny vrátane SK; jina-v2 je CC-BY-NC
    "low": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
    "high": "BAAI/bge-reranker-v2-m3",
}


def detect_ram_gb() -> int:
    page_size = os.sysconf("SC_PAGE_SIZE")
    phys_pages = os.sysconf("SC_PHYS_PAGES")
    return int(page_size * phys_pages / 1024**3)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="ignore")

    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"
    llm_provider: Literal["ollama", "openai"] = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    # OpenAI-kompatibilné API (OpenAI, Groq, Together, vLLM, LM Studio…)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    # instruct variant — hybrid thinking qwen3:4b ignoruje think=false a leakuje
    # reasoning do odpovede; instruct je non-thinking
    answer_model: str = "qwen3:4b-instruct"
    extract_model: str = "qwen3:1.7b"
    ram_profile: RamProfile = "auto"
    embedding_model: str | None = None  # None → podľa profilu
    chunk_max_tokens: int = 450
    graph_expansion: bool = True  # A/B flag — merateľný prínos grafu (eval)
    graph_weight: float = 1.0  # váha grafových kandidátov v RRF fúzii
    graph_relational_only: bool = True  # graf len pri otázkach na súvislosti/porovnanie
    agent_mode: bool = True  # kompozičné otázky cez agent slučku s nástrojmi
    # cross-encoder nad fúzovanými kandidátmi; None → zapnutý na high profile
    # (eval: MRR +9 b., ~0.9 s na otázku na M-series GPU)
    rerank: bool | None = None
    reranker_model: str | None = None  # None → podľa profilu
    # LLM kontext ku každému chunku pri ingeste (Anthropic Contextual Retrieval);
    # eval: MRR 72 → 81 % bez latencie pri dotaze — cena je len čas ingestu
    contextual_chunks: bool = True
    # experimentálne: ColPali vizuálny retrieval strán PDF (uv sync --extra visual)
    visual_retrieval: bool = False
    visual_model: str = "vidore/colSmol-256M"
    tracing: bool = True  # OTel spany do SQLite (vstavaný prehliadač)
    otlp_endpoint: str | None = None  # napr. http://localhost:6006/v1/traces (Phoenix)
    otlp_headers: str = ""  # "Authorization=Basic xyz,X-Other=1" (Langfuse)
    auth_enabled: bool = False  # prihlasovanie + prístupové skupiny k dokumentom
    session_ttl_hours: int = 168
    admin_email: str | None = None  # prvý admin pri štarte, ak žiadny používateľ neexistuje
    admin_password: str | None = None
    cookie_secure: bool = False  # True za HTTPS proxy
    injection_guard: bool = True  # obrana proti pokynom schovaným v dokumentoch
    # brána pred routerom: tajomstvá, vnútro systému a otázky mimo účelu → pevná odpoveď
    scope_guard: bool = True
    # LLM klasifikátor rozsahu pri lookup otázke bez signálu z knižnice
    scope_classifier: bool = True
    inbox_sync: bool = True  # data/inbox ↔ knižnica (nové, zmenené, zmazané súbory)
    inbox_interval_s: float = 5.0
    mcp_allowed_hosts: str = ""  # ďalšie Host hlavičky pre /mcp, napr. "backend:*,rag.lan:*"

    @property
    def effective_profile(self) -> Literal["low", "high"]:
        if self.ram_profile != "auto":
            return self.ram_profile
        return "high" if detect_ram_gb() >= HIGH_PROFILE_MIN_GB else "low"

    @property
    def effective_embedding_model(self) -> str:
        return self.embedding_model or PROFILE_EMBEDDING_MODELS[self.effective_profile]

    @property
    def effective_rerank(self) -> bool:
        return self.rerank if self.rerank is not None else self.effective_profile == "high"

    @property
    def effective_reranker_model(self) -> str:
        return self.reranker_model or PROFILE_RERANKER_MODELS[self.effective_profile]

    @property
    def pdf_dir(self) -> Path:
        return self.data_dir / "pdfs"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def tables_dir(self) -> Path:
        return self.data_dir / "tables"

    @property
    def lancedb_dir(self) -> Path:
        return self.data_dir / "lancedb"

    @property
    def generated_dir(self) -> Path:
        return self.data_dir / "generated"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "app.db"

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.pdf_dir,
            self.parsed_dir,
            self.tables_dir,
            self.generated_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

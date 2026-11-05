import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

RamProfile = Literal["auto", "low", "high"]

# The high profile enables heavier models (bge-m3, reranker); threshold chosen so
# that 16 GB machines stay on the low profile — bge-m3 + LLM together is tight there.
HIGH_PROFILE_MIN_GB = 32

PROFILE_EMBEDDING_MODELS: dict[str, str] = {
    "low": "intfloat/multilingual-e5-small",
    "high": "BAAI/bge-m3",
}
PROFILE_RERANKER_MODELS: dict[str, str] = {
    # mMiniLMv2 (Apache-2.0, ~120M) — multilingual including SK; jina-v2 is CC-BY-NC
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
    # minimum num_ctx for every Ollama call — a model reloads whenever num_ctx changes, so one
    # size (the largest any caller needs, 12288 for answers) keeps it loaded; lower it on a
    # tight machine at the cost of occasional reloads
    ollama_num_ctx: int = 12288
    # OpenAI-compatible API (OpenAI, Groq, Together, vLLM, LM Studio…)
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    # instruct variant — the hybrid-thinking qwen3:4b ignores think=false and leaks
    # reasoning into the answer; instruct is non-thinking
    answer_model: str = "qwen3:4b-instruct"
    extract_model: str = "qwen3:1.7b"
    ram_profile: RamProfile = "auto"
    embedding_model: str | None = None  # None → by profile
    chunk_max_tokens: int = 450
    graph_expansion: bool = True  # A/B flag — measurable benefit of the graph (eval)
    graph_weight: float = 1.0  # weight of graph candidates in RRF fusion
    graph_relational_only: bool = True  # graph only for relationship/comparison questions
    agent_mode: bool = True  # compositional questions via an agent loop with tools
    # cross-encoder over the fused candidates; None → enabled on the high profile
    # (eval: MRR +9 pts, ~0.9 s per question on an M-series GPU)
    rerank: bool | None = None
    reranker_model: str | None = None  # None → by profile
    # LLM context for every chunk at ingest (Anthropic Contextual Retrieval);
    # eval: MRR 72 → 81 % with no query-time latency — the only cost is ingest time
    contextual_chunks: bool = True
    # experimental: ColPali visual retrieval of PDF pages (uv sync --extra visual)
    visual_retrieval: bool = False
    visual_model: str = "vidore/colSmol-256M"
    tracing: bool = True  # OTel spans into SQLite (built-in viewer)
    otlp_endpoint: str | None = None  # e.g. http://localhost:6006/v1/traces (Phoenix)
    otlp_headers: str = ""  # "Authorization=Basic xyz,X-Other=1" (Langfuse)
    auth_enabled: bool = False  # login + document access groups
    session_ttl_hours: int = 168
    admin_email: str | None = None  # first admin at startup if no user exists
    admin_password: str | None = None
    cookie_secure: bool = False  # True behind an HTTPS proxy
    injection_guard: bool = True  # defence against instructions hidden in documents
    # gate before the router: secrets, system internals and off-purpose questions → fixed answer
    scope_guard: bool = True
    # LLM scope classifier for a lookup question with no signal from the library
    scope_classifier: bool = True
    inbox_sync: bool = True  # data/inbox ↔ library (new, changed, deleted files)
    inbox_interval_s: float = 5.0
    mcp_allowed_hosts: str = ""  # extra Host headers for /mcp, e.g. "backend:*,rag.lan:*"

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

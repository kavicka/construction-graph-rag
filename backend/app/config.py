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


def detect_ram_gb() -> int:
    page_size = os.sysconf("SC_PAGE_SIZE")
    phys_pages = os.sysconf("SC_PHYS_PAGES")
    return int(page_size * phys_pages / 1024**3)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="ignore")

    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"
    ollama_base_url: str = "http://localhost:11434"
    # instruct variant — hybrid thinking qwen3:4b ignoruje think=false a leakuje
    # reasoning do odpovede; instruct je non-thinking
    answer_model: str = "qwen3:4b-instruct"
    extract_model: str = "qwen3:1.7b"
    ram_profile: RamProfile = "auto"
    embedding_model: str | None = None  # None → podľa profilu
    chunk_max_tokens: int = 450
    graph_expansion: bool = True  # A/B flag — merateľný prínos grafu (eval)
    agent_mode: bool = True  # kompozičné otázky cez agent slučku s nástrojmi

    @property
    def effective_profile(self) -> Literal["low", "high"]:
        if self.ram_profile != "auto":
            return self.ram_profile
        return "high" if detect_ram_gb() >= HIGH_PROFILE_MIN_GB else "low"

    @property
    def effective_embedding_model(self) -> str:
        return self.embedding_model or PROFILE_EMBEDDING_MODELS[self.effective_profile]

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
    def sqlite_path(self) -> Path:
        return self.data_dir / "app.db"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.pdf_dir, self.parsed_dir, self.tables_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()

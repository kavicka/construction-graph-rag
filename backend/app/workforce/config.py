"""Nastavenia modulu Firma — rovnaký prefix RAG_ a .env ako hlavné nastavenia."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import get_settings

# STT: eval (eval/WORKFORCE.md) — turbo má na slovenčine nižší WER (5,3 % vs 10,8 %)
# aj kratší čas ako plný large-v3, preto oba profily; LLM: high = väčší model na nejasné
# dokumenty, low = 8–16 GB, nech sa zmestí vedľa embeddingov
PROFILE_STT_MODELS = {
    "low": "mlx-community/whisper-large-v3-turbo",
    "high": "mlx-community/whisper-large-v3-turbo",
}
PROFILE_WORKFORCE_LLM = {
    "low": "qwen3:4b-instruct",
    "high": "qwen3:30b-a3b-instruct-2507-q4_K_M",
}


class WorkforceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RAG_", extra="ignore")

    stt_model: str | None = None  # None → podľa RAM profilu
    tts_voice: str = "sk_SK-lili-medium"
    workforce_model: str | None = None  # extrakcia výpisov a úloh; None → podľa profilu
    daily_summary: bool = True  # automatický súhrnný denný výpis
    daily_summary_time: str = "19:00"
    audio_retention_days: int = 30
    pin_max_attempts: int = 5
    pin_lock_minutes: int = 15
    worker_session_days: int = 90  # pracovník na mobile ostáva prihlásený
    hours_tolerance: float = 0.5  # rozdiel odpracovaného času a súčtu hodín pri úlohách

    @property
    def effective_stt_model(self) -> str:
        return self.stt_model or PROFILE_STT_MODELS[get_settings().effective_profile]

    @property
    def effective_llm(self) -> str:
        return self.workforce_model or PROFILE_WORKFORCE_LLM[get_settings().effective_profile]

    @property
    def workforce_dir(self) -> Path:
        return get_settings().data_dir / "workforce"

    @property
    def audio_dir(self) -> Path:
        return self.workforce_dir / "audio"

    @property
    def reports_dir(self) -> Path:
        return self.workforce_dir / "reports"

    @property
    def tts_cache_dir(self) -> Path:
        return self.workforce_dir / "tts"

    @property
    def voices_dir(self) -> Path:
        return get_settings().data_dir / "voices"

    def ensure_dirs(self) -> None:
        for d in (self.audio_dir, self.reports_dir, self.tts_cache_dir, self.voices_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_workforce_settings() -> WorkforceSettings:
    return WorkforceSettings()

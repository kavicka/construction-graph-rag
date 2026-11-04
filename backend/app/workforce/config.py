"""Settings of the Firma module — same RAG_ prefix and .env as the main settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import get_settings

# STT: eval (eval/WORKFORCE.md) — turbo has a lower WER on Slovak (5.3 % vs 10.8 %) and shorter time
# than full large-v3, hence both profiles; LLM: high = larger model for unclear documents, low =
# 8–16 GB, so it fits next to the embeddings
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

    stt_model: str | None = None  # None → by RAM profile
    tts_voice: str = "sk_SK-lili-medium"
    workforce_model: str | None = None  # extraction of reports and tasks; None → by profile
    daily_summary: bool = True  # automatic daily summary report
    daily_summary_time: str = "19:00"
    audio_retention_days: int = 30
    pin_max_attempts: int = 5
    pin_lock_minutes: int = 15
    worker_session_days: int = 90  # worker stays logged in on mobile
    hours_tolerance: float = 0.5  # allowed gap between worked time and the sum of task hours

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

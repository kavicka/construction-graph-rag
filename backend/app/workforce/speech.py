"""Speech: transcription (mlx-whisper) and synthesis (Piper) — both local, models are loaded lazily.

Both libraries are optional (`uv sync --extra voice`; mlx-whisper only on Apple Silicon). Without
Piper the questions are text-only, without Whisper transcription returns 501 with an explanation.

Transcription: any format from a phone (webm/opus, mp4/aac, ogg, m4a, wav) → ffmpeg → 16 kHz mono
wav → Whisper with Slovak and a site vocabulary (initial_prompt: site and task names), so it better
catches jargon. One transcription at a time — the MLX model shares GPU memory."""

import hashlib
import importlib.util
import logging
import re
import subprocess
import tempfile
import threading
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.workforce.config import get_workforce_settings

logger = logging.getLogger(__name__)

_stt_lock = threading.Lock()
_tts_lock = threading.Lock()
MAX_AUDIO_SECONDS = 15 * 60
PROMPT_MAX_CHARS = 600

# Whisper invents subtitle phrases on silence — they do not belong in the report
HALLUCINATIONS = re.compile(
    r"(titulky|subtitles|ďakujem za pozornosť|ďakujem za sledovanie|www\.\S+|amara\.org)"
    r"[^.!?]*[.!?]?",
    re.IGNORECASE,
)


class SpeechError(RuntimeError):
    pass


class SpeechUnavailable(HTTPException):
    """An optional dependency is missing — the API returns 501, background transcription records a
    report error."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=501, detail=detail)

    def __str__(self) -> str:
        return str(self.detail)


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def stt_available() -> bool:
    return _installed("mlx_whisper")


def to_wav16k(src: Path, dst: Path) -> float:
    """→ duration in seconds. ffmpeg handles anything a browser or phone records."""
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-t",
                str(MAX_AUDIO_SECONDS),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(dst),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise SpeechError("ffmpeg nie je nainštalovaný (brew install ffmpeg)") from exc
    except subprocess.CalledProcessError as exc:
        raise SpeechError(f"nahrávku sa nepodarilo prečítať: {exc.stderr[-200:]!r}") from exc
    with wave.open(str(dst), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or 16000)


def clean_transcript(text: str) -> str:
    text = HALLUCINATIONS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


# Time pattern in words: without it turbo transcribes „o pol siedmej“ as „o 05.07“ (verified on
# recordings) — Whisper keeps the prompt's style and the time then stays in words we can read.
TIME_STYLE = "Časy hovor slovami: o pol siedmej, o siedmej, o štvrť na päť, o pol štvrtej."


def vocabulary_prompt(sites: list[str], tasks: list[str], names: list[str]) -> str:
    """Short "vocabulary" for Whisper — names it would otherwise get wrong, and the time pattern."""
    parts = [TIME_STYLE]
    if sites:
        parts.append("Stavby: " + ", ".join(dict.fromkeys(sites)) + ".")
    if tasks:
        parts.append("Úlohy: " + ", ".join(dict.fromkeys(tasks)) + ".")
    if names:
        parts.append("Kolegovia: " + ", ".join(dict.fromkeys(names)) + ".")
    return " ".join(parts)[:PROMPT_MAX_CHARS]


def transcribe(audio_path: Path, prompt: str | None = None) -> str:
    """Audio file → Slovak text."""
    if not stt_available():
        raise SpeechUnavailable(
            "prepis reči nie je na tomto serveri dostupný (vyžaduje Apple Silicon "
            "a `uv sync --extra voice`) — napíš to textom"
        )
    import mlx_whisper

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        seconds = to_wav16k(audio_path, wav)
        if seconds < 0.3:
            return ""
        with _stt_lock:
            result: dict[str, Any] = mlx_whisper.transcribe(
                str(wav),
                path_or_hf_repo=get_workforce_settings().effective_stt_model,
                language="sk",
                initial_prompt=prompt or None,
                condition_on_previous_text=False,
                hallucination_silence_threshold=2.0,
                word_timestamps=True,  # required by hallucination_silence_threshold
            )
    return clean_transcript(str(result.get("text", "")))


@lru_cache
def _voice() -> Any:
    if not _installed("piper"):
        raise SpeechUnavailable(
            "syntéza reči nie je na tomto serveri nainštalovaná (`uv sync --extra voice`)"
        )
    from piper import PiperVoice

    settings = get_workforce_settings()
    model = settings.voices_dir / f"{settings.tts_voice}.onnx"
    if not model.exists():
        raise SpeechError(
            f"hlas {settings.tts_voice} chýba — stiahni ho: uv run python -m "
            f"piper.download_voices --download-dir {settings.voices_dir} {settings.tts_voice}"
        )
    return PiperVoice.load(model)


def tts_available() -> bool:
    if not _installed("piper"):
        return False
    settings = get_workforce_settings()
    return (settings.voices_dir / f"{settings.tts_voice}.onnx").exists()


def synthesize(text: str) -> Path:
    """Text → wav (cached by voice and text — the same question is not generated again)."""
    settings = get_workforce_settings()
    settings.ensure_dirs()
    key = hashlib.sha256(f"{settings.tts_voice}\n{text}".encode()).hexdigest()[:32]
    out = settings.tts_cache_dir / f"{key}.wav"
    if out.exists():
        return out
    voice = _voice()
    tmp = out.with_suffix(".part")
    with _tts_lock, wave.open(str(tmp), "wb") as wf:
        voice.synthesize_wav(text, wf)
    tmp.replace(out)
    return out

"""Reč: prepis (mlx-whisper) a syntéza (Piper) — obe lokálne, modely sa načítajú lenivo.

Prepis: ľubovoľný formát z mobilu (webm/opus, mp4/aac, ogg, m4a, wav) → ffmpeg → 16 kHz mono
wav → Whisper so slovenčinou a slovníkom stavby (initial_prompt: názvy stavieb a úloh),
aby lepšie trafil žargón. Jeden prepis naraz — MLX model zdieľa GPU pamäť.
"""

import hashlib
import logging
import re
import subprocess
import tempfile
import threading
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.workforce.config import get_workforce_settings

logger = logging.getLogger(__name__)

_stt_lock = threading.Lock()
_tts_lock = threading.Lock()
MAX_AUDIO_SECONDS = 15 * 60
PROMPT_MAX_CHARS = 600

# Whisper si na tichu vymýšľa titulkové frázy — tie do výpisu nepatria
HALLUCINATIONS = re.compile(
    r"(titulky|subtitles|ďakujem za pozornosť|ďakujem za sledovanie|www\.\S+|amara\.org)"
    r"[^.!?]*[.!?]?",
    re.IGNORECASE,
)


class SpeechError(RuntimeError):
    pass


def to_wav16k(src: Path, dst: Path) -> float:
    """→ dĺžka v sekundách. ffmpeg zvládne všetko, čo nahrá prehliadač alebo telefón."""
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


# Vzor časov slovami: bez neho turbo prepíše „o pol siedmej“ ako „o 05.07“ (overené na
# nahrávkach) — Whisper drží štýl promptu a čas potom ostane v slovách, ktoré vieme čítať.
TIME_STYLE = "Časy hovor slovami: o pol siedmej, o siedmej, o štvrť na päť, o pol štvrtej."


def vocabulary_prompt(sites: list[str], tasks: list[str], names: list[str]) -> str:
    """Krátky „slovník“ pre Whisper — názvy, ktoré inak prepíše zle, a vzor časov."""
    parts = [TIME_STYLE]
    if sites:
        parts.append("Stavby: " + ", ".join(dict.fromkeys(sites)) + ".")
    if tasks:
        parts.append("Úlohy: " + ", ".join(dict.fromkeys(tasks)) + ".")
    if names:
        parts.append("Kolegovia: " + ", ".join(dict.fromkeys(names)) + ".")
    return " ".join(parts)[:PROMPT_MAX_CHARS]


def transcribe(audio_path: Path, prompt: str | None = None) -> str:
    """Audio súbor → slovenský text."""
    import mlx_whisper  # type: ignore[import-untyped]

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
                word_timestamps=True,  # vyžaduje ho hallucination_silence_threshold
            )
    return clean_transcript(str(result.get("text", "")))


@lru_cache
def _voice() -> Any:
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
    settings = get_workforce_settings()
    return (settings.voices_dir / f"{settings.tts_voice}.onnx").exists()


def synthesize(text: str) -> Path:
    """Text → wav (cache podľa hlasu a textu — tá istá otázka sa negeneruje znova)."""
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

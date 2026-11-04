"""Optional voice (the `voice` extra): without Whisper/Piper nothing breaks, it just turns off."""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import voice
from app.workforce import speech


def _missing(*names: str) -> Callable[[str], bool]:
    real = speech._installed
    return lambda module: module not in names and real(module)


def test_transcribe_without_whisper_is_501(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(speech, "_installed", _missing("mlx_whisper"))
    with pytest.raises(speech.SpeechUnavailable) as err:
        speech.transcribe(tmp_path / "x.webm")
    assert err.value.status_code == 501
    assert "prepis reči" in str(err.value)  # readable error in the background log too


def test_no_piper_means_text_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech, "_installed", _missing("piper"))
    speech._voice.cache_clear()
    assert speech.tts_available() is False
    with pytest.raises(speech.SpeechUnavailable):
        speech._voice()
    speech._voice.cache_clear()


def test_voice_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech, "stt_available", lambda: False)
    monkeypatch.setattr(speech, "tts_available", lambda: True)
    app = FastAPI()
    app.include_router(voice.router, prefix="/api")
    resp = TestClient(app).get("/api/voice")
    assert resp.status_code == 200
    assert resp.json() == {"stt": False, "tts": True}

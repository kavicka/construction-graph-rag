"""Speech pipeline without the voice extra: ffmpeg wrapper, transcript cleanup, Whisper and
Piper calls (replaced by fakes), TTS cache and availability checks."""

import subprocess
import sys
import wave
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.workforce import speech
from app.workforce.config import get_workforce_settings


def _write_wav(path: Path, seconds: float, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(seconds * rate))


def _fake_ffmpeg(monkeypatch: pytest.MonkeyPatch, seconds: float, calls: list[list[str]]) -> None:
    """Stand-in for the ffmpeg binary: writes a silent wav of the wanted length to dst."""

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(cmd)
        assert kwargs["check"] is True and kwargs["capture_output"] is True
        _write_wav(Path(cmd[-1]), seconds)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(speech.subprocess, "run", run)


# --- clean_transcript ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Zajtra o pol siedmej na Rovinke.", "Zajtra o pol siedmej na Rovinke."),
        ("Murári prídu o siedmej. Titulky vytvoril Janko.", "Murári prídu o siedmej."),
        ("Ďakujem za pozornosť.", ""),
        ("Betonáž hotová. Ďakujem za sledovanie!", "Betonáž hotová."),
        # the rest of the sentence after a trigger phrase is dropped, up to its punctuation
        ("Hotovo. Pozri www.example.sk a potom pokračuj.", "Hotovo. Pozri"),
        ("Amara.org subtitles by the community", ""),
        ("  veľa \n\n  medzier\t tu  ", "veľa medzier tu"),
        ("", ""),
    ],
)
def test_clean_transcript(raw: str, expected: str) -> None:
    assert speech.clean_transcript(raw) == expected


# --- vocabulary_prompt -----------------------------------------------------------------


def test_vocabulary_prompt_starts_with_time_style_and_dedupes() -> None:
    prompt = speech.vocabulary_prompt(
        ["Rovinka", "Agátová", "Rovinka"], ["Betonáž"], ["Jozef Mikuš", "Jozef Mikuš"]
    )
    assert prompt.startswith(speech.TIME_STYLE)
    assert prompt.count("Rovinka") == 1
    assert "Stavby: Rovinka, Agátová." in prompt
    assert "Úlohy: Betonáž." in prompt
    assert prompt.count("Jozef Mikuš") == 1


def test_vocabulary_prompt_omits_empty_sections() -> None:
    assert speech.vocabulary_prompt([], [], []) == speech.TIME_STYLE
    only_tasks = speech.vocabulary_prompt([], ["Murovanie"], [])
    assert "Stavby" not in only_tasks and "Kolegovia" not in only_tasks


def test_vocabulary_prompt_is_capped() -> None:
    prompt = speech.vocabulary_prompt([f"Stavba {i}" for i in range(500)], [], [])
    assert len(prompt) == speech.PROMPT_MAX_CHARS


# --- to_wav16k -------------------------------------------------------------------------


def test_to_wav16k_runs_ffmpeg_and_returns_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    _fake_ffmpeg(monkeypatch, 2.5, calls)
    seconds = speech.to_wav16k(tmp_path / "in.webm", tmp_path / "out.wav")
    assert seconds == pytest.approx(2.5)
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-t") + 1] == str(speech.MAX_AUDIO_SECONDS)  # long recordings are cut
    assert cmd[cmd.index("-i") + 1] == str(tmp_path / "in.webm")


def test_to_wav16k_without_ffmpeg_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(*a: Any, **k: Any) -> None:
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(speech.subprocess, "run", missing)
    with pytest.raises(speech.SpeechError, match="ffmpeg nie je nainštalovaný"):
        speech.to_wav16k(tmp_path / "in.webm", tmp_path / "out.wav")


def test_to_wav16k_unreadable_recording(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*a: Any, **k: Any) -> None:
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"x" * 500 + b"Invalid data found")

    monkeypatch.setattr(speech.subprocess, "run", fail)
    with pytest.raises(speech.SpeechError, match="nahrávku sa nepodarilo prečítať") as err:
        speech.to_wav16k(tmp_path / "in.webm", tmp_path / "out.wav")
    assert "Invalid data found" in str(err.value)
    assert len(str(err.value)) < 300  # only the tail of stderr is surfaced


def test_to_wav16k_zero_framerate_falls_back_to_16k(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeWav:
        def __enter__(self) -> "FakeWav":
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def getnframes(self) -> int:
            return 32000

        def getframerate(self) -> int:
            return 0

    monkeypatch.setattr(speech.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(speech.wave, "open", lambda *a, **k: FakeWav())
    assert speech.to_wav16k(tmp_path / "a", tmp_path / "b") == pytest.approx(2.0)


# --- transcribe ------------------------------------------------------------------------


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"text": " Zajtra o pol siedmej. Titulky vytvoril X. ", "calls": []}
    module = ModuleType("mlx_whisper")

    def transcribe(path: str, **kwargs: Any) -> dict[str, Any]:
        state["calls"].append((path, kwargs))
        return {"text": state["text"]}

    module.transcribe = transcribe  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    monkeypatch.setattr(speech, "stt_available", lambda: True)
    return state


def test_transcribe_passes_slovak_settings_and_cleans_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: dict[str, Any]
) -> None:
    _fake_ffmpeg(monkeypatch, 3.0, [])
    text = speech.transcribe(tmp_path / "nahravka.webm", prompt="Stavby: Rovinka.")
    assert text == "Zajtra o pol siedmej."
    _, kwargs = fake_whisper["calls"][0]
    assert kwargs["language"] == "sk"
    assert kwargs["initial_prompt"] == "Stavby: Rovinka."
    assert kwargs["path_or_hf_repo"] == get_workforce_settings().effective_stt_model
    assert kwargs["condition_on_previous_text"] is False
    # hallucination_silence_threshold only works together with word timestamps
    assert kwargs["word_timestamps"] is True


def test_transcribe_empty_prompt_becomes_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: dict[str, Any]
) -> None:
    _fake_ffmpeg(monkeypatch, 3.0, [])
    speech.transcribe(tmp_path / "a.webm", prompt="")
    assert fake_whisper["calls"][0][1]["initial_prompt"] is None


def test_transcribe_very_short_audio_skips_whisper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: dict[str, Any]
) -> None:
    _fake_ffmpeg(monkeypatch, 0.1, [])  # accidental tap on the record button
    assert speech.transcribe(tmp_path / "a.webm") == ""
    assert fake_whisper["calls"] == []


def test_transcribe_missing_text_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: dict[str, Any]
) -> None:
    _fake_ffmpeg(monkeypatch, 2.0, [])
    fake_whisper["text"] = ""
    assert speech.transcribe(tmp_path / "a.webm") == ""


def test_transcribe_ffmpeg_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        speech.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError())
    )
    with pytest.raises(speech.SpeechError):
        speech.transcribe(tmp_path / "a.webm")
    assert fake_whisper["calls"] == []


# --- availability ----------------------------------------------------------------------


def test_stt_available_follows_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(speech, "_installed", lambda m: m == "mlx_whisper")
    assert speech.stt_available() is True
    monkeypatch.setattr(speech, "_installed", lambda m: False)
    assert speech.stt_available() is False


def test_installed_detects_missing_module() -> None:
    assert speech._installed("json") is True
    assert speech._installed("definitely_not_a_module_xyz") is False


def test_tts_available_requires_piper_and_voice_file(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_workforce_settings()
    monkeypatch.setattr(speech, "_installed", lambda m: True)
    assert speech.tts_available() is False  # piper installed but voice not downloaded
    settings.ensure_dirs()
    (settings.voices_dir / f"{settings.tts_voice}.onnx").write_bytes(b"onnx")
    assert speech.tts_available() is True
    monkeypatch.setattr(speech, "_installed", lambda m: False)
    assert speech.tts_available() is False  # voice file alone is not enough


# --- Piper voice loading and synthesis -------------------------------------------------


@pytest.fixture
def fake_piper(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"loaded": [], "synth": []}

    class FakeVoice:
        def synthesize_wav(self, text: str, wf: wave.Wave_write) -> None:
            state["synth"].append(text)
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(b"\x01\x00" * 100)

    class PiperVoice:
        @staticmethod
        def load(model: Path) -> FakeVoice:
            state["loaded"].append(model)
            return FakeVoice()

    module = ModuleType("piper")
    module.PiperVoice = PiperVoice  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", module)
    monkeypatch.setattr(speech, "_installed", lambda m: True)
    speech._voice.cache_clear()
    yield state
    speech._voice.cache_clear()


def test_voice_missing_model_file_has_download_hint(fake_piper: dict[str, Any]) -> None:
    settings = get_workforce_settings()
    with pytest.raises(speech.SpeechError, match="chýba") as err:
        speech._voice()
    assert settings.tts_voice in str(err.value)
    assert "piper.download_voices" in str(err.value)
    assert fake_piper["loaded"] == []


def test_voice_is_loaded_once(fake_piper: dict[str, Any]) -> None:
    settings = get_workforce_settings()
    settings.ensure_dirs()
    model = settings.voices_dir / f"{settings.tts_voice}.onnx"
    model.write_bytes(b"onnx")
    assert speech._voice() is speech._voice()
    assert fake_piper["loaded"] == [model]


def test_synthesize_writes_wav_and_caches(fake_piper: dict[str, Any]) -> None:
    settings = get_workforce_settings()
    settings.ensure_dirs()
    (settings.voices_dir / f"{settings.tts_voice}.onnx").write_bytes(b"onnx")

    first = speech.synthesize("Dobrý deň, tu je otázka.")
    assert first.parent == settings.tts_cache_dir and first.suffix == ".wav"
    with wave.open(str(first), "rb") as wf:
        assert wf.getnframes() == 100
    assert not first.with_suffix(".part").exists()  # temp file renamed into place

    again = speech.synthesize("Dobrý deň, tu je otázka.")
    assert again == first
    assert fake_piper["synth"] == ["Dobrý deň, tu je otázka."]  # second call served from cache

    other = speech.synthesize("Iný text.")
    assert other != first
    assert len(fake_piper["synth"]) == 2


def test_synthesize_cache_key_depends_on_voice(
    fake_piper: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_workforce_settings()
    settings.ensure_dirs()
    (settings.voices_dir / f"{settings.tts_voice}.onnx").write_bytes(b"onnx")
    a = speech.synthesize("Ahoj")
    monkeypatch.setenv("RAG_TTS_VOICE", "sk_SK-iny-medium")
    get_workforce_settings.cache_clear()
    new_settings = get_workforce_settings()
    (new_settings.voices_dir / "sk_SK-iny-medium.onnx").write_bytes(b"onnx")
    speech._voice.cache_clear()
    b = speech.synthesize("Ahoj")
    assert a != b  # changing the voice must not serve the old voice's audio


def test_synthesize_without_voice_raises_clear_error(fake_piper: dict[str, Any]) -> None:
    with pytest.raises(speech.SpeechError):
        speech.synthesize("Ahoj")
    cache_dir = get_workforce_settings().tts_cache_dir
    assert list(cache_dir.glob("*")) == []  # nothing half-written is left behind

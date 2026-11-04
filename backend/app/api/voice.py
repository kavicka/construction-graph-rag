"""Voice availability — the frontend uses it to hide the microphone and question playback."""

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.workforce import speech

router = APIRouter()


class VoiceStatus(BaseModel):
    stt: bool  # transcription (mlx-whisper, Apple Silicon + extra voice)
    tts: bool  # synthesis (Piper from the voice extra + downloaded voice)


@router.get("/voice")
async def voice_status() -> VoiceStatus:
    return VoiceStatus(
        stt=await run_in_threadpool(speech.stt_available),
        tts=await run_in_threadpool(speech.tts_available),
    )

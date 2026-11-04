"""Dostupnosť hlasu — frontend podľa nej skryje mikrofón a prehrávanie otázok."""

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.workforce import speech

router = APIRouter()


class VoiceStatus(BaseModel):
    stt: bool  # prepis (mlx-whisper, Apple Silicon + extra voice)
    tts: bool  # syntéza (Piper z extra voice + stiahnutý hlas)


@router.get("/voice")
async def voice_status() -> VoiceStatus:
    return VoiceStatus(
        stt=await run_in_threadpool(speech.stt_available),
        tts=await run_in_threadpool(speech.tts_available),
    )

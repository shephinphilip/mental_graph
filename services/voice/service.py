"""Voice turn adapter: STT → existing ``run_chat_graph`` → TTS.

Background extraction is scheduled by the caller after audio is sent.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import get_settings, logger
from integrations.sarvam import SarvamError, synthesize_pcm, transcribe_wav
from services.extraction import run_background_extraction
from services.graph import run_chat_graph
from services.language_preferences import resolve_response_language
from services.voice.audio import normalize_for_stt
from services.voice.session import VoiceSession, user_id_hash
from services.voice.tts import tts_language_code


@dataclass
class VoiceTurnResult:
    transcript: str
    reply: str
    action_cards: List[Dict[str, Any]] = field(default_factory=list)
    pcm: bytes = b""
    sample_rate: int = 24_000
    stt_latency_ms: int = 0
    llm_latency_ms: int = 0
    tts_latency_ms: int = 0
    tts_first_audio_ms: int = 0
    total_latency_ms: int = 0
    tts_failed: bool = False
    duplicate: bool = False


async def transcribe_audio(
    data: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> Dict[str, Any]:
    wav = normalize_for_stt(data, filename=filename, content_type=content_type)
    result = await transcribe_wav(wav)
    return {"success": True, "text": result["text"]}


async def run_voice_turn(
    *,
    user_id: str,
    session_id: str,
    audio: bytes,
    db: AsyncIOMotorDatabase,
    voice_session_id: str = "",
    request_id: str = "",
    filename: str = "",
    content_type: str = "",
    live_session: Optional[VoiceSession] = None,
    allow_raw_pcm: bool = True,
) -> VoiceTurnResult:
    """One spoken turn through the existing chat graph, then TTS."""
    settings = get_settings()
    t0 = time.perf_counter()
    wav = normalize_for_stt(
        audio,
        filename=filename,
        content_type=content_type,
        allow_raw_pcm=allow_raw_pcm,
    )
    stt = await transcribe_wav(wav)
    transcript = (stt.get("text") or "").strip()
    if not transcript:
        raise ValueError("No speech was recognized.")
    if live_session is not None and not live_session.mark_turn(transcript):
        return VoiceTurnResult(
            transcript=transcript,
            reply="",
            duplicate=True,
            stt_latency_ms=int(stt.get("latency_ms") or 0),
            total_latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    t1 = time.perf_counter()
    result = await asyncio.wait_for(
        run_chat_graph(
            user_id=user_id,
            session_id=session_id,
            user_message=transcript,
            db=db,
        ),
        timeout=float(settings.VOICE_LLM_TIMEOUT),
    )
    t2 = time.perf_counter()
    reply = result.get("reply") or ""
    action_cards = result.get("action_cards") or []

    language = await resolve_response_language(db, user_id)
    tts_failed = False
    pcm = b""
    sample_rate = 24_000
    tts_latency = 0
    first_audio = 0
    try:
        spoken = await synthesize_pcm(
            reply,
            target_language_code=tts_language_code(language["resolved_language"]),
        )
        pcm = spoken["pcm"]
        sample_rate = int(spoken.get("sample_rate") or 24_000)
        tts_latency = int(spoken.get("latency_ms") or 0)
        first_audio = int(spoken.get("first_audio_ms") or 0)
    except SarvamError:
        tts_failed = True
        logger.warning(
            "voice_tts_failed user=%s voice_session=%s request_id=%s",
            user_id_hash(user_id),
            voice_session_id,
            request_id,
        )

    total_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "voice_turn user=%s voice_session=%s chat_session=%s request_id=%s "
        "stt_ms=%s llm_ms=%s tts_ms=%s first_audio_ms=%s total_ms=%s tts_failed=%s",
        user_id_hash(user_id),
        voice_session_id,
        session_id,
        request_id,
        stt.get("latency_ms"),
        int((t2 - t1) * 1000),
        tts_latency,
        first_audio,
        total_ms,
        tts_failed,
    )
    return VoiceTurnResult(
        transcript=transcript,
        reply=reply,
        action_cards=action_cards,
        pcm=pcm,
        sample_rate=sample_rate,
        stt_latency_ms=int(stt.get("latency_ms") or 0),
        llm_latency_ms=int((t2 - t1) * 1000),
        tts_latency_ms=tts_latency,
        tts_first_audio_ms=first_audio,
        total_latency_ms=total_ms,
        tts_failed=tts_failed,
        duplicate=False,
    )


async def schedule_extraction(
    *,
    user_id: str,
    session_id: str,
    transcript: str,
    reply: str,
    db: AsyncIOMotorDatabase,
) -> None:
    try:
        await run_background_extraction(
            user_id=user_id,
            session_id=session_id,
            message=transcript,
            reply=reply,
            db=db,
        )
    except Exception:
        logger.exception(
            "voice extraction failed user=%s chat_session=%s",
            user_id_hash(user_id),
            session_id,
        )

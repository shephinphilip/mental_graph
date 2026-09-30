"""Single Sarvam speech client. STT and TTS only — not a second LLM brain.

The API key is read from Settings and never returned to callers.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Dict, Optional

import httpx

from config.config import get_settings, logger
from services.voice.audio import decode_base64_audio

_provider_slots: Optional[asyncio.Semaphore] = None


class SarvamError(RuntimeError):
    """Provider call failed. Message is safe for logs, not a secret dump."""


def _semaphore() -> asyncio.Semaphore:
    global _provider_slots
    if _provider_slots is None:
        _provider_slots = asyncio.Semaphore(max(1, int(get_settings().VOICE_PROVIDER_CONCURRENCY)))
    return _provider_slots


def _headers() -> Dict[str, str]:
    key = (get_settings().SARVAM_API_KEY or "").strip()
    if not key:
        raise SarvamError("Speech service is not configured.")
    return {"api-subscription-key": key}


def _safe_status_error(status_code: int, body: str) -> SarvamError:
    snippet = (body or "").replace("\n", " ")[:160]
    if get_settings().SARVAM_API_KEY and get_settings().SARVAM_API_KEY in snippet:
        snippet = "[redacted]"
    return SarvamError(f"Speech provider returned {status_code}.")


async def transcribe_wav(wav_bytes: bytes) -> Dict[str, Any]:
    """
    POST speech-to-text. Language is detected automatically — no hint.

    Returns ``{"text": str, "latency_ms": int, "model": str}``.
    """
    settings = get_settings()
    files = {
        "file": ("speech.wav", wav_bytes, "audio/wav"),
    }
    data = {"model": settings.SARVAM_STT_MODEL}
    started = time.perf_counter()
    async with _semaphore():
        try:
            async with httpx.AsyncClient(timeout=settings.VOICE_STT_TIMEOUT) as client:
                response = await client.post(
                    settings.SARVAM_STT_URL,
                    headers=_headers(),
                    files=files,
                    data=data,
                )
        except httpx.TimeoutException as exc:
            raise SarvamError("Speech-to-text timed out.") from exc
        except httpx.HTTPError as exc:
            raise SarvamError("Speech-to-text is unavailable.") from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code >= 400:
        logger.warning("Sarvam STT failed status=%s", response.status_code)
        raise _safe_status_error(response.status_code, response.text)
    payload = response.json()
    text = (
        payload.get("transcript")
        or payload.get("text")
        or ""
    )
    if not isinstance(text, str):
        text = str(text or "")
    logger.info(
        "sarvam_stt model=%s latency_ms=%s chars=%s",
        settings.SARVAM_STT_MODEL,
        latency_ms,
        len(text.strip()),
    )
    return {
        "text": text.strip(),
        "latency_ms": latency_ms,
        "model": settings.SARVAM_STT_MODEL,
        "provider": "sarvam",
    }


def _split_tts_text(text: str, limit: int = 500) -> list[str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= limit:
        return [cleaned]
    parts: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= limit:
            parts.append(remaining)
            break
        cut = remaining.rfind(". ", 0, limit)
        if cut < 40:
            cut = remaining.rfind(" ", 0, limit)
        if cut < 40:
            cut = limit
        else:
            cut += 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [part for part in parts if part]


async def synthesize_pcm(text: str, *, target_language_code: str) -> Dict[str, Any]:
    """
    POST text-to-speech. Returns raw 16-bit PCM and the provider sample rate.

    Sarvam currently returns a complete audio payload (often base64 WAV).
    We decode it and hand PCM chunks to the WebSocket — we do not wait to
    accumulate extra copies beyond that one payload.
    """
    settings = get_settings()
    segments = _split_tts_text(text)
    if not segments:
        return {
            "pcm": b"",
            "sample_rate": 24_000,
            "latency_ms": 0,
            "first_audio_ms": 0,
            "model": settings.SARVAM_TTS_MODEL,
            "provider": "sarvam",
        }

    pcm_parts: list[bytes] = []
    sample_rate = 24_000
    started = time.perf_counter()
    first_audio_ms = 0
    headers = {**_headers(), "Content-Type": "application/json"}

    async with _semaphore():
        try:
            async with httpx.AsyncClient(timeout=settings.VOICE_TTS_TIMEOUT) as client:
                for segment in segments:
                    body = {
                        "text": segment,
                        "target_language_code": target_language_code,
                        "speaker": settings.VOICE_TTS_SPEAKER,
                        "model": settings.SARVAM_TTS_MODEL,
                        "speech_sample_rate": 24000,
                        "enable_preprocessing": True,
                    }
                    try:
                        response = await client.post(
                            settings.SARVAM_TTS_URL,
                            headers=headers,
                            json=body,
                        )
                    except httpx.TimeoutException as exc:
                        raise SarvamError("Text-to-speech timed out.") from exc
                    except httpx.HTTPError as exc:
                        raise SarvamError("Text-to-speech is unavailable.") from exc
                    if response.status_code >= 400:
                        logger.warning("Sarvam TTS failed status=%s", response.status_code)
                        raise _safe_status_error(response.status_code, response.text)
                    if first_audio_ms == 0:
                        first_audio_ms = int((time.perf_counter() - started) * 1000)
                    payload = response.json()
                    audios = payload.get("audios") or []
                    encoded = audios[0] if audios else payload.get("audio") or ""
                    if not encoded:
                        raise SarvamError("Text-to-speech returned no audio.")
                    raw = base64.b64decode(encoded)
                    pcm, sample_rate = decode_base64_audio(raw)
                    pcm_parts.append(pcm)
        except SarvamError:
            raise

    latency_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "sarvam_tts model=%s latency_ms=%s first_audio_ms=%s segments=%s",
        settings.SARVAM_TTS_MODEL,
        latency_ms,
        first_audio_ms,
        len(segments),
    )
    return {
        "pcm": b"".join(pcm_parts),
        "sample_rate": sample_rate,
        "latency_ms": latency_ms,
        "first_audio_ms": first_audio_ms,
        "model": settings.SARVAM_TTS_MODEL,
        "provider": "sarvam",
    }

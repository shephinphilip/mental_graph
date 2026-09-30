"""Validate and normalize microphone audio for Sarvam STT.

Sarvam Saarika v2.5 expects 16-bit PCM, 16 kHz, mono WAV.
Browser clips are converted at most once. Raw audio is never logged.
"""

from __future__ import annotations

import audioop
import io
import struct
import wave
from dataclasses import dataclass
from typing import Optional

from config.config import get_settings

SUPPORTED_WAV_MAGIC = b"RIFF"
PCM_FORMAT_TAG = 1
STT_SAMPLE_RATE = 16_000
STT_CHANNELS = 1
STT_SAMPLE_WIDTH = 2
TTS_SAMPLE_RATE = 24_000


class AudioValidationError(ValueError):
    """Client audio cannot be accepted."""


@dataclass(frozen=True)
class WavInfo:
    channels: int
    sample_width: int
    sample_rate: int
    frames: int
    duration_seconds: float
    pcm: bytes


def _settings_limits() -> tuple[int, int]:
    settings = get_settings()
    return int(settings.MAX_AUDIO_FILE_SIZE), int(settings.MAX_AUDIO_DURATION_SECONDS)


def pcm_to_wav(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap linear PCM in a WAV container."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def read_wav(data: bytes) -> WavInfo:
    if len(data) < 12 or data[:4] != SUPPORTED_WAV_MAGIC:
        raise AudioValidationError("Unsupported audio format. Send a WAV file.")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            pcm = wav.readframes(frames)
            comptype = wav.getcomptype()
    except wave.Error as exc:
        raise AudioValidationError("Audio file is not a valid WAV.") from exc
    if comptype not in {"NONE", "not compressed"}:
        raise AudioValidationError("Compressed WAV is not supported.")
    if channels < 1 or sample_width < 1 or sample_rate < 1:
        raise AudioValidationError("Audio file has an invalid WAV header.")
    duration = frames / float(sample_rate) if sample_rate else 0.0
    return WavInfo(
        channels=channels,
        sample_width=sample_width,
        sample_rate=sample_rate,
        frames=frames,
        duration_seconds=duration,
        pcm=pcm,
    )


def _to_mono_s16(pcm: bytes, channels: int, sample_width: int) -> bytes:
    if sample_width != STT_SAMPLE_WIDTH:
        pcm = audioop.lin2lin(pcm, sample_width, STT_SAMPLE_WIDTH)
    if channels > 1:
        pcm = audioop.tomono(pcm, STT_SAMPLE_WIDTH, 0.5, 0.5)
    return pcm


def _resample_s16(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    if source_rate == target_rate:
        return pcm
    converted, _ = audioop.ratecv(pcm, STT_SAMPLE_WIDTH, 1, source_rate, target_rate, None)
    return converted


def validate_audio_bytes(data: bytes, *, filename: str = "", content_type: str = "") -> None:
    """Reject empty, oversized, or non-audio payloads. MIME is not trusted."""
    del filename, content_type
    max_size, _ = _settings_limits()
    if not data:
        raise AudioValidationError("Audio file is empty.")
    if len(data) > max_size:
        raise AudioValidationError("Audio file is too large.")


def normalize_for_stt(
    data: bytes,
    *,
    filename: str = "",
    content_type: str = "",
    allow_raw_pcm: bool = False,
) -> bytes:
    """
    Return a 16-bit PCM 16 kHz mono WAV.

    Resamples at most once. HTTP uploads must be WAV. Raw PCM16 is only
    accepted when ``allow_raw_pcm`` is set (WebSocket frames).
    """
    validate_audio_bytes(data, filename=filename, content_type=content_type)
    max_size, max_duration = _settings_limits()

    if data[:4] == SUPPORTED_WAV_MAGIC:
        info = read_wav(data)
        if info.duration_seconds > max_duration:
            raise AudioValidationError("Audio is longer than the allowed duration.")
        if info.duration_seconds <= 0 or not info.pcm:
            raise AudioValidationError("Audio file has no samples.")
        pcm = _to_mono_s16(info.pcm, info.channels, info.sample_width)
        pcm = _resample_s16(pcm, info.sample_rate, STT_SAMPLE_RATE)
        return pcm_to_wav(pcm, sample_rate=STT_SAMPLE_RATE)

    if allow_raw_pcm and len(data) % 2 == 0:
        duration = (len(data) / 2) / float(STT_SAMPLE_RATE)
        if duration > max_duration:
            raise AudioValidationError("Audio is longer than the allowed duration.")
        if duration <= 0:
            raise AudioValidationError("Audio file has no samples.")
        if len(data) > max_size:
            raise AudioValidationError("Audio file is too large.")
        return pcm_to_wav(data, sample_rate=STT_SAMPLE_RATE)

    raise AudioValidationError("Unsupported audio format. Send a WAV file.")


def wav_to_pcm(data: bytes) -> tuple[bytes, int]:
    """Strip a WAV header. Returns (pcm, sample_rate)."""
    if data[:4] == SUPPORTED_WAV_MAGIC:
        info = read_wav(data)
        pcm = _to_mono_s16(info.pcm, info.channels, info.sample_width)
        return pcm, info.sample_rate
    return data, TTS_SAMPLE_RATE


def decode_base64_audio(raw: bytes) -> tuple[bytes, int]:
    """Accept WAV bytes or raw PCM from a provider response."""
    return wav_to_pcm(raw)


def pcm_chunker(pcm: bytes, chunk_bytes: Optional[int] = None):
    settings = get_settings()
    size = int(chunk_bytes or settings.VOICE_AUDIO_CHUNK_BYTES)
    size = max(2, size - (size % 2))
    for index in range(0, len(pcm), size):
        yield pcm[index : index + size]


def wav_duration_seconds(data: bytes) -> float:
    if data[:4] != SUPPORTED_WAV_MAGIC:
        return (len(data) / 2) / float(STT_SAMPLE_RATE)
    return read_wav(data).duration_seconds


def silence_wav(duration_ms: int = 200) -> bytes:
    """Tiny valid WAV used by tests. Not used as a substitute for user audio."""
    frames = int(STT_SAMPLE_RATE * (duration_ms / 1000.0))
    pcm = struct.pack("<" + "h" * frames, *([0] * frames))
    return pcm_to_wav(pcm, sample_rate=STT_SAMPLE_RATE)

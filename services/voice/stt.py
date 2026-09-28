"""Standalone speech-to-text. Does not invoke the chat brain."""

from __future__ import annotations

from typing import Any, Dict

from integrations.sarvam import transcribe_wav
from services.voice.audio import normalize_for_stt


async def transcribe_upload(
    data: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> Dict[str, Any]:
    wav = normalize_for_stt(data, filename=filename, content_type=content_type)
    result = await transcribe_wav(wav)
    return {"success": True, "text": result["text"]}

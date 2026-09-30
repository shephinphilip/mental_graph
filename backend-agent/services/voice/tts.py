"""Map stored preferred_language onto Sarvam Bulbul language codes."""

from __future__ import annotations

from services.language_preferences import normalize_language

SARVAM_TTS_LANGUAGE = {
    "ENGLISH": "en-IN",
    "HINDI": "hi-IN",
    "HINGLISH": "en-IN",
    "TELUGU": "te-IN",
    "TAMIL": "ta-IN",
    "MALAYALAM": "ml-IN",
    "KANNADA": "kn-IN",
    "BENGALI": "bn-IN",
    "GUJARATI": "gu-IN",
    "PUNJABI": "pa-IN",
    "ODIA": "od-IN",
    "URDU": "ur-IN",
}


def tts_language_code(preferred_language: str) -> str:
    parsed = normalize_language(preferred_language) or "ENGLISH"
    return SARVAM_TTS_LANGUAGE.get(parsed, "en-IN")

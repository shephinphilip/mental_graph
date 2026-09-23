"""
Response language comes from users.preferred_language.

A short cache is only for when that read fails. The current message
does not choose the language.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from services.users import get_by_identifier

logger = logging.getLogger(__name__)

SUPPORTED = (
    "ENGLISH",
    "HINDI",
    "HINGLISH",
    "TELUGU",
    "TAMIL",
    "MALAYALAM",
    "KANNADA",
    "BENGALI",
    "GUJARATI",
    "PUNJABI",
    "ODIA",
    "URDU",
)

_SCRIPTS = {
    "ENGLISH": "LATIN",
    "HINDI": "DEVANAGARI",
    "HINGLISH": "ROMAN",
    "TELUGU": "TELUGU",
    "TAMIL": "TAMIL",
    "MALAYALAM": "MALAYALAM",
    "KANNADA": "KANNADA",
    "BENGALI": "BENGALI",
    "GUJARATI": "GUJARATI",
    "PUNJABI": "GURMUKHI",
    "ODIA": "ODIA",
    "URDU": "NASTALIQ",
}

_NAMES = {
    "ENGLISH": "English",
    "HINDI": "Hindi",
    "HINGLISH": "Hinglish",
    "TELUGU": "Telugu",
    "TAMIL": "Tamil",
    "MALAYALAM": "Malayalam",
    "KANNADA": "Kannada",
    "BENGALI": "Bengali",
    "GUJARATI": "Gujarati",
    "PUNJABI": "Punjabi",
    "ODIA": "Odia",
    "URDU": "Urdu",
}

_RANK_CODES = {
    "ENGLISH": "en",
    "HINDI": "hi",
    "HINGLISH": "hi-Latn",
    "TELUGU": "te",
    "TAMIL": "ta",
    "MALAYALAM": "ml",
    "KANNADA": "kn",
    "BENGALI": "bn",
    "GUJARATI": "gu",
    "PUNJABI": "pa",
    "ODIA": "or",
    "URDU": "ur",
}

_CACHE: Dict[str, tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 60

_CRISIS = {
    "ENGLISH": "If this feels like too much, you do not have to handle it alone. Help is here:",
    "HINDI": "अगर अभी बहुत भारी लग रहा है, तो अकेले मत रहो. मदद यहीं है:",
    "HINGLISH": "Agar abhi bahut heavy lag raha hai, akela mat raho. Help yahin hai:",
    "TELUGU": "ఇప్పుడు చాలా భారంగా అనిపిస్తే, ఒంటరిగా ఉండకు. సహాయం ఉంది:",
    "TAMIL": "இப்போது ரொம்ப கஷ்டமா இருந்தா, தனியா இருக்க வேண்டாம். உதவி இருக்கு:",
    "MALAYALAM": "ഇപ്പോൾ വളരെ ഭാരമായി തോന്നുന്നുണ്ടെങ്കിൽ, ഒറ്റയ്ക്ക് നിൽക്കണ്ട. സഹായം ഉണ്ട്:",
    "KANNADA": "ಈಗ ತುಂಬಾ ಭಾರವಾಗಿ ಅನಿಸಿದರೆ, ಒಂಟಿಯಾಗಿ ಇರಬೇಡ. ಸಹಾಯ ಇದೆ:",
    "BENGALI": "এখন খুব ভারী লাগলে, একা থেকো না. সাহায্য আছে:",
    "GUJARATI": "હમણાં બહુ ભારે લાગે તો એકલા ન રહેશો. મદદ છે:",
    "PUNJABI": "ਜੇ ਹੁਣ ਬਹੁਤ ਭਾਰੀ ਲੱਗੇ, ਇਕੱਲੇ ਨਾ ਰਹੋ. ਮਦਦ ਹੈ:",
    "ODIA": "ଏବେ ବହୁତ ଭାରୀ ଲାଗିଲେ, ଏକା ରୁହନ୍ତୁ ନାହିଁ. ସାହାଯ୍ୟ ଅଛି:",
    "URDU": "اگر اب بہت بھاری لگے تو اکیلے مت رہو. مدد موجود ہے:",
}

_PRACTICE = {
    "ENGLISH": "A short practice that fits this sitting",
    "HINDI": "इस बातचीत के लिए एक छोटा सा अभ्यास",
    "HINGLISH": "Is baat ke liye ek chhota sa practice",
    "TELUGU": "ఈ కూర్చోవడానికి సరిపోయే చిన్న అభ్యాసం",
    "TAMIL": "இந்த உட்காருதலுக்கு பொருந்தும் ஒரு சின்ன பழக்கம்",
    "MALAYALAM": "ഈ ഇരുത്തത്തിന് ചേരുന്ന ഒരു ചെറിയ പരിശീലനം",
    "KANNADA": "ಈ ಕೂತುಕೊಳ್ಳುವಿಕೆಗೆ ಹೊಂದುವ ಒಂದು ಚಿಕ್ಕ ಅಭ್ಯಾಸ",
    "BENGALI": "এই বসার জন্য মানানসই একটা ছোট অনুশীলন",
    "GUJARATI": "આ બેઠકને અનુકૂળ એક નાનો અભ્યાસ",
    "PUNJABI": "ਇਸ ਬੈਠਕ ਲਈ ਇੱਕ ਛੋਟਾ ਅਭਿਆਸ",
    "ODIA": "ଏହି ବସିବା ପାଇଁ ଏକ ଛୋଟ ଅଭ୍ୟାସ",
    "URDU": "اس بیٹھک کے لیے ایک چھوٹی سی مشق",
}

_HELPLINES = (
    "Tele-MANAS 14416, Vandrevala +91 9999 666 555, "
    "KIRAN 1800-599-0019, AASRA +91 9820466726."
)


def normalize_language(value: Any) -> Optional[str]:
    """Accept only the stored enum, in any letter case. Nothing else."""
    text = str(value or "").strip().upper()
    if text in SUPPORTED:
        return text
    return None


def language_rank_code(value: Any) -> str:
    """Short code for meditation matching. Unknown values stay English."""
    parsed = normalize_language(value)
    if parsed:
        return _RANK_CODES[parsed]
    folded = str(value or "").strip().casefold()
    for code in _RANK_CODES.values():
        if folded == code.casefold():
            return code
    return "en"


def language_instruction(resolved: Dict[str, str]) -> str:
    language = resolved.get("resolved_language") or "ENGLISH"
    if language not in SUPPORTED:
        language = "ENGLISH"
    if language == "HINGLISH":
        return (
            "RESPONSE LANGUAGE:\n"
            "The user's selected language is HINGLISH.\n"
            "Respond entirely in natural conversational Hindi using Roman script, "
            "with ordinary WhatsApp-style Hindi-English mixing.\n"
            "Do not write Devanagari.\n"
            "Do not switch to English-only because the current message is in English.\n"
            "Sound like a short WhatsApp chat: warm, simple, not a textbook and not a translation.\n"
            "Keep phone numbers, official names, and product names unchanged.\n"
            "Use this same language for the whole reply. Do not change language halfway."
        )
    if language == "ENGLISH":
        return (
            "RESPONSE LANGUAGE:\n"
            "The user's selected language is ENGLISH.\n"
            "Respond entirely in casual WhatsApp-style English.\n"
            "Do not switch language because the current message is in another language.\n"
            "Sound warm and short, not like an essay or a clinical note.\n"
            "Keep phone numbers and official names unchanged.\n"
            "Use this same language for the whole reply. Do not change language halfway."
        )
    name = _NAMES[language]
    return (
        "RESPONSE LANGUAGE:\n"
        f"The user's selected language is {language}.\n"
        f"Respond entirely in natural conversational {name}, in its usual script.\n"
        "Write like a WhatsApp chat: warm, short, simple. Not formal literary language "
        "and not a word-for-word translation.\n"
        "Do not switch to English because the current message is written in English.\n"
        "Do not mix English words into the sentence. A product name, an official name, "
        "or a phone number may stay as written.\n"
        "Use this same language for the whole reply, including reports, task titles, "
        "and any practice you mention. Do not change language halfway."
    )


def crisis_message(language: str) -> str:
    chosen = normalize_language(language) or "ENGLISH"
    return f"{_CRISIS[chosen]} {_HELPLINES}"


def explain_practice(language: str, title: str) -> str:
    chosen = normalize_language(language) or "ENGLISH"
    name = title or "this practice"
    return f"{_PRACTICE[chosen]}: {name}."


def _cache_get(user_id: str) -> Optional[str]:
    row = _CACHE.get(user_id)
    if not row:
        return None
    language, expires = row
    if expires < time.monotonic():
        _CACHE.pop(user_id, None)
        return None
    return language


def remember_language(user_id: str, language: str) -> None:
    parsed = normalize_language(language)
    if user_id and parsed:
        _CACHE[user_id] = (parsed, time.monotonic() + _CACHE_TTL_SECONDS)


def clear_language_cache(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(user_id, None)


async def resolve_response_language(db, user_id: str) -> Dict[str, str]:
    """
    Database first. Cache only if that read fails. Otherwise English.

    The text of the current message is not an input.
    """
    language = "ENGLISH"
    source = "FALLBACK"
    try:
        user = await get_by_identifier(db, user_id)
        raw = (user or {}).get("preferred_language") if isinstance(user, dict) else None
        parsed = normalize_language(raw)
        if parsed:
            language = parsed
            source = "DATABASE"
            remember_language(user_id, language)
        elif raw:
            logger.warning(
                "Invalid preferred_language=%s for user=%s; using ENGLISH",
                raw,
                user_id,
            )
        else:
            logger.info("No preferred_language for user=%s; using ENGLISH", user_id)
    except Exception:
        logger.exception("Preferred language lookup failed for user=%s", user_id)
        cached = _cache_get(user_id)
        if cached:
            language = cached
            source = "CACHE"
    resolved = {
        "preferred_language": language,
        "resolved_language": language,
        "resolved_script": _SCRIPTS[language],
        "source": source,
    }
    logger.info(
        "language_resolution preferred_language=%s resolved_language=%s resolved_script=%s source=%s",
        resolved["preferred_language"],
        resolved["resolved_language"],
        resolved["resolved_script"],
        resolved["source"],
    )
    return resolved


def language_write_target(authenticated_id: str, claimed_user_id: Optional[str]) -> str:
    """The token decides whose preference changes. A body user id cannot."""
    if claimed_user_id and claimed_user_id != authenticated_id:
        raise PermissionError("User identity mismatch")
    return authenticated_id


def resolve_language_and_script(language: str) -> Dict[str, str]:
    parsed = normalize_language(language) or "ENGLISH"
    return {"language": parsed, "script": _SCRIPTS[parsed]}

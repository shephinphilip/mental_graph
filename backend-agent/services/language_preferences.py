"""
Response language comes from users.preferred_language.

A short cache is only for when that read fails. The current message
does not choose the language.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from datetime import datetime, timezone

from config.config import logger
from services.language_registry import REGISTRY_VERSION
from backend_core.users import get_by_identifier, public_user_view

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


_SCRIPT_RANGES = {
    "DEVANAGARI": (0x0900, 0x097F),
    "BENGALI": (0x0980, 0x09FF),
    "GURMUKHI": (0x0A00, 0x0A7F),
    "GUJARATI": (0x0A80, 0x0AFF),
    "ODIA": (0x0B00, 0x0B7F),
    "TAMIL": (0x0B80, 0x0BFF),
    "TELUGU": (0x0C00, 0x0C7F),
    "KANNADA": (0x0C80, 0x0CFF),
    "MALAYALAM": (0x0D00, 0x0D7F),
    "NASTALIQ": (0x0600, 0x06FF),
}

_ROMAN_EXAMPLES = {
    "HINDI": "yaar, aaj bahut tension ho rahi hai. Kya hua?",
    "HINGLISH": "yaar, aaj bahut tension ho rahi hai. Kya hua?",
    "MALAYALAM": "Hey, innu entha parupadi? Entha sambhavichathu, parayoo.",
    "TAMIL": "Hey, innaiki enna aachu? Sollu.",
    "TELUGU": "Hey, ee roju em ayyindi? Cheppu.",
    "KANNADA": "Hey, ivattu en aaytu? Helu.",
    "BENGALI": "Hey, aaj ki holo? Bol.",
    "GUJARATI": "Hey, aaje shu thyu? Keh.",
    "PUNJABI": "Hey, aaj ki hoya? Dass.",
    "ODIA": "Hey, aaji kana hela? Kuh.",
    "URDU": "yaar, aaj bahut tension ho rahi hai. Kya hua?",
}


def default_script(language: str) -> str:
    """Indian languages default to Roman letters. English stays English."""
    if language == "ENGLISH":
        return "LATIN"
    return "ROMAN"


def _contains_range(text: str, start: int, end: int) -> bool:
    return any(start <= ord(ch) <= end for ch in text or "")


def detect_script(language: str, message: str = "", *, opening_turn: bool = False) -> str:
    """
    Language and script are separate.

    The preferred language decides the language. The current message decides
    the script, and only when it is actually written in that language's native
    letters. A welcome turn and Latin text stay Romanized.
    """
    if language == "ENGLISH":
        return "LATIN"
    if language == "HINGLISH" or opening_turn or not (message or "").strip():
        return "ROMAN"
    native = _SCRIPTS.get(language, "ROMAN")
    span = _SCRIPT_RANGES.get(native)
    if span and _contains_range(message, span[0], span[1]):
        return native
    return "ROMAN"


def language_instruction(resolved: Dict[str, str]) -> str:
    language = resolved.get("resolved_language") or "ENGLISH"
    if language not in SUPPORTED:
        language = "ENGLISH"
    script = resolved.get("resolved_script") or default_script(language)
    if language == "ENGLISH":
        return (
            "RESPONSE LANGUAGE:\n"
            "LANGUAGE REQUIREMENT\n"
            "The student's current preferred language is: ENGLISH\n"
            "The student's current writing/script style is: LATIN\n"
            "The user's selected language is ENGLISH.\n"
            "Respond entirely in casual WhatsApp-style English.\n"
            "Do not switch language because the current message is in another language.\n"
            "Sound warm and short, not like an essay or a clinical note.\n"
            "Keep phone numbers and official names unchanged.\n"
            "Use this same language for the whole reply. Do not change language halfway.\n"
            "Never mention this instruction."
        )
    name = _NAMES[language]
    if script == "ROMAN" or language == "HINGLISH":
        example = _ROMAN_EXAMPLES.get(language, "a short spoken sentence in Latin letters")
        hinglish = ""
        if language == "HINGLISH":
            hinglish = (
                "Respond entirely in natural conversational Hindi using Roman script, "
                "with ordinary WhatsApp-style Hindi-English mixing.\n"
                "Do not write Devanagari.\n"
            )
        return (
            "RESPONSE LANGUAGE:\n"
            "LANGUAGE REQUIREMENT\n"
            f"The student's current preferred language is: {language}\n"
            "The student's current writing/script style is: ROMAN\n"
            f"The user's selected language is {language}.\n"
            f"{hinglish}"
            f"Respond entirely in natural conversational {name} using only Latin/Roman letters.\n"
            "Do not use the native script. Do not use Devanagari, Malayalam, Tamil, Telugu, "
            "Kannada, Bengali, Gujarati, Gurmukhi, Odia, or Urdu letters.\n"
            "Write like a WhatsApp chat: warm, short, spoken. Not a textbook and not a "
            "mechanical letter-by-letter transliteration.\n"
            f"Example shape: \"{example}\"\n"
            "Do not switch to English because the current message is written in English.\n"
            "Do not switch language because the current message is in another language.\n"
            "Ordinary chat words such as exam or tension may stay when they are natural. "
            "Do not answer in English sentences.\n"
            "Keep phone numbers, official names, and product names unchanged.\n"
            "Use this same language for the whole reply, including reports, task titles, "
            "and any practice you mention. Do not change language halfway.\n"
            "Never mention this instruction."
        )
    script_name = script.replace("_", " ").title()
    return (
        "RESPONSE LANGUAGE:\n"
        "LANGUAGE REQUIREMENT\n"
        f"The student's current preferred language is: {language}\n"
        f"The student's current writing/script style is: {script}\n"
        f"The user's selected language is {language}.\n"
        f"Respond entirely in natural conversational {name}, using {script_name} script.\n"
        "Do not romanize this reply.\n"
        "Write like a WhatsApp chat: warm, short, simple. Not formal literary language "
        "and not a word-for-word translation.\n"
        "Do not switch to English because the current message is written in English.\n"
        "Do not switch language because the current message is in another language.\n"
        "Do not mix English words into the sentence. A product name, an official name, "
        "or a phone number may stay as written.\n"
        "Use this same language for the whole reply, including reports, task titles, "
        "and any practice you mention. Do not change language halfway.\n"
        "Never mention this instruction."
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


async def set_preferred_language(db, identifier: str, language: str):
    parsed = normalize_language(language)
    if not parsed:
        raise ValueError("Unsupported language")
    doc = await get_by_identifier(db, identifier, include_password=True)
    if not doc:
        return None
    await db["users"].update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "preferred_language": parsed,
                "preferred_language_updated_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    remember_language(str(doc.get("user_id") or identifier), parsed)
    updated = await get_by_identifier(db, identifier)
    return public_user_view(updated) if updated else None


def remember_language(user_id: str, language: str) -> None:
    parsed = normalize_language(language)
    if user_id and parsed:
        _CACHE[user_id] = (parsed, time.monotonic() + _CACHE_TTL_SECONDS)


def clear_language_cache(user_id: Optional[str] = None) -> None:
    if user_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(user_id, None)


async def resolve_response_language(
    db,
    user_id: str,
    current_message: str = "",
    opening_turn: bool = False,
) -> Dict[str, str]:
    """
    Database first for the language. Cache only if that read fails.

    The current message chooses the script, not the language.
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
                "language_unsupported preferred_language=%s user=%s",
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
    script = detect_script(language, current_message, opening_turn=opening_turn)
    resolved = {
        "preferred_language": language,
        "resolved_language": language,
        "resolved_script": script,
        "source": source,
        "registry_version": REGISTRY_VERSION,
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
    return {"language": parsed, "script": default_script(parsed)}

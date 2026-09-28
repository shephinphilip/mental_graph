"""One safety class per message. Not risk intensity, trajectory, GDS, or care band."""

from __future__ import annotations

import re
from enum import Enum

from services.apm import contains_crisis_signal

_INTENT = (
    r"(?:want to|wanna|going to|gonna|feel like|feeling like|tempted to|"
    r"curious about|curious to|plan to|planning to|thinking of|thinking about|"
    r"how (?:do|can) i|how to|tell me how)"
)

_VIOLENCE = re.compile(
    _INTENT
    + r".{0,50}(?:beat (?:him|her|them|someone) up|hurt (?:him|her|them|someone)|"
    r"\bstab\b|\bpunch\b|kill (?:him|her|them|someone)|"
    r"attack (?:him|her|them))"
    r"|(?:maarna hai|maar dunga|maar doon|मारना है|मार दूंगा)",
    re.IGNORECASE,
)

_SELF_HARM = re.compile(
    r"(?:want to|feel like|going to|keep|keeps|thinking about).{0,30}"
    r"(?:hitting myself|hit myself|burn myself|burning myself|starve myself)"
    r"|hitting myself|hit myself|burn myself|burning myself",
    re.IGNORECASE,
)

_ABUSE = re.compile(
    r"(?:dad|mom|father|mother|uncle|parent|stepdad|stepmom).{0,40}"
    r"(?:hits|hit|beats|beat|abuses|hurts) me"
    r"|(?:beats me|hits me at home|abuses me)",
    re.IGNORECASE,
)

_SEXUAL_EXPLOITATION = re.compile(
    r"touched me inappropriately|sexual abuse|groom(?:ing|ed) me|"
    r"forced me to (?:touch|kiss|undress)",
    re.IGNORECASE,
)

_SEXUAL_CONTENT = re.compile(
    r"talk dirty|send nudes|sexual roleplay|have sex with me",
    re.IGNORECASE,
)

_SUBSTANCE = re.compile(
    _INTENT
    + r".{0,40}(?:\bsmoke\b|\bsmoking\b|\bcigarette\b|\bvape\b|\balcohol\b|"
    r"\bbeer\b|\bweed\b|\bdrugs?\b|get high)"
    r"|(?:smoke karne ka mann|smoke kar|cigarette pi|drugs lena|nasha karna)",
    re.IGNORECASE,
)

_MISCONDUCT = re.compile(
    _INTENT
    + r".{0,40}(?:\bcheat(?:ing)?\b|\bshoplift\b)"
    r"|(?:cheat karna chahta|cheat karna chahti|nakal karni|नकल कर)",
    re.IGNORECASE,
)

_JAILBREAK = re.compile(
    r"ignore (?:your|all|previous) rules|dan mode|ignore previous instructions",
    re.IGNORECASE,
)

_NEGATED = re.compile(
    r"(?:don'?t|do not|never|not going to|nahi|nahin|नहीं)\s+"
    r"(?:really\s+)?(?:want to|wanna|feel like|going to|gonna)",
    re.IGNORECASE,
)


class SafetyClass(str, Enum):
    CRISIS_KEYWORD = "CRISIS_KEYWORD"
    SELF_HARM = "SELF_HARM"
    VIOLENCE = "VIOLENCE"
    ABUSE = "ABUSE"
    SEXUAL_EXPLOITATION = "SEXUAL_EXPLOITATION"
    SEXUAL_CONTENT = "SEXUAL_CONTENT"
    SUBSTANCE = "SUBSTANCE"
    MISCONDUCT = "MISCONDUCT"
    JAILBREAK = "JAILBREAK"
    NONE = "NONE"


def _negated(text: str, match: re.Match[str]) -> bool:
    start = max(0, match.start() - 40)
    return _NEGATED.search(text[start:match.end()]) is not None


def _hits(pattern: re.Pattern[str], text: str) -> bool:
    return any(not _negated(text, match) for match in pattern.finditer(text))


def classify_message(message: str) -> SafetyClass:
    """First match wins. Do not add a class without a spec change."""
    text = message or ""
    if contains_crisis_signal(text):
        return SafetyClass.CRISIS_KEYWORD
    if _hits(_SELF_HARM, text):
        return SafetyClass.SELF_HARM
    if _hits(_VIOLENCE, text):
        return SafetyClass.VIOLENCE
    if _hits(_ABUSE, text):
        return SafetyClass.ABUSE
    if _hits(_SEXUAL_EXPLOITATION, text):
        return SafetyClass.SEXUAL_EXPLOITATION
    if _hits(_SEXUAL_CONTENT, text):
        return SafetyClass.SEXUAL_CONTENT
    if _hits(_SUBSTANCE, text):
        return SafetyClass.SUBSTANCE
    if _hits(_MISCONDUCT, text):
        return SafetyClass.MISCONDUCT
    if _hits(_JAILBREAK, text):
        return SafetyClass.JAILBREAK
    return SafetyClass.NONE


def blocks_ordinary_support(label: SafetyClass) -> bool:
    return label is not SafetyClass.NONE

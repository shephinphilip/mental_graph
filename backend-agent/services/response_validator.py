"""Enforcement after generation. Prompt text is not this layer."""

from __future__ import annotations

from dataclasses import dataclass

VALIDATOR_VERSION = "2026.09.1"
MAX_REGENERATIONS = 1
FALLBACK_REPLY = "I'm here with you. We can stay with what you just said."

_DIAGNOSIS = (
    "you have depression",
    "you have anxiety",
    "you have adhd",
    "you will fail",
    "you're going to fail",
    "you are going to fail",
    "i am a psychiatrist",
    "i'm a psychiatrist",
    "i am your psychiatrist",
    "i'm your psychiatrist",
    "under 3 minutes",
    "in under 3 minutes",
    "sessions available in under",
)

_NATIVE_RANGES = (
    (0x0900, 0x097F),
    (0x0980, 0x09FF),
    (0x0A00, 0x0A7F),
    (0x0A80, 0x0AFF),
    (0x0B00, 0x0B7F),
    (0x0B80, 0x0BFF),
    (0x0C00, 0x0C7F),
    (0x0C80, 0x0CFF),
    (0x0D00, 0x0D7F),
    (0x0600, 0x06FF),
)


@dataclass(frozen=True)
class Validation:
    ok: bool
    reason: str = ""


def _native_script(text: str) -> bool:
    return any(start <= ord(ch) <= end for ch in text for start, end in _NATIVE_RANGES)


def validate_reply(
    text: str,
    *,
    script: str = "LATIN",
    pattern_supplied: bool = False,
) -> Validation:
    body = text or ""
    lowered = body.casefold()
    for phrase in _DIAGNOSIS:
        if phrase in lowered:
            return Validation(False, "false_clinical_claim")
    if body.count("?") > 1:
        return Validation(False, "excessive_questions")
    if script == "ROMAN" and _native_script(body):
        return Validation(False, "roman_script_violation")
    if not pattern_supplied and "stored pattern" in lowered:
        return Validation(False, "invented_pattern")
    return Validation(True)

"""Active languages are the ones the chat resolver may emit. Planned codes are not."""

from __future__ import annotations

from dataclasses import dataclass

REGISTRY_VERSION = "2026.09.1"

# Official languages not yet backed by crisis copy, roman examples, and tests.
PLANNED = (
    "ASSAMESE",
    "BODO",
    "DOGRI",
    "KASHMIRI",
    "KONKANI",
    "MAITHILI",
    "MANIPURI",
    "MARATHI",
    "NEPALI",
    "SANSKRIT",
    "SANTALI",
    "SINDHI",
)


@dataclass(frozen=True)
class LanguageEntry:
    code: str
    status: str  # ACTIVE | PLANNED


def entry_for(code: str) -> LanguageEntry | None:
    from services.language_preferences import SUPPORTED

    text = str(code or "").strip().upper()
    if not text:
        return None
    if text in SUPPORTED:
        return LanguageEntry(text, "ACTIVE")
    if text in PLANNED:
        return LanguageEntry(text, "PLANNED")
    return None


def is_active(code: str) -> bool:
    found = entry_for(code)
    return bool(found and found.status == "ACTIVE")

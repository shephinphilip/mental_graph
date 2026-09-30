"""Receptivity from the current turn only — never forbidden engagement telemetry."""

from __future__ import annotations

from typing import Iterable, Sequence

from services.apm import temporal_bucket
from services.engagement_guard import reject_engagement_features
from services.inner_council import _OVERLOAD_MARKERS, _contains_any, _word_count
from services.proactive.schemas import ReceptivityState, UNAVAILABLE_RECEPTIVITY_SIGNALS

_OPEN_MARKERS = (
    "feel",
    "feeling",
    "felt",
    "wonder",
    "journal",
    "wrote",
    "been thinking",
    "kind of",
    "sort of",
    "i guess",
    "maybe",
    "stressed",
    "heavy",
    "hard",
    "worried",
    "nervous",
    "tired",
    "exam",
    "test",
    "presentation",
    "project",
    "assignment",
)

_CLOSED_MARKERS = (
    "i'm fine",
    "im fine",
    "i am fine",
    "it's nothing",
    "its nothing",
    "nothing much",
    "all good",
    "i'm okay",
    "im okay",
    "i'm ok",
    "im ok",
    "idk",
    "i don't know",
    "k",
    "ok",
    "okay",
    "fine",
    "nothing",
    "nm",
)

_JOURNAL_PRESENT = "RECENT JOURNAL CONTEXT"


def unavailable_signals() -> tuple[str, ...]:
    return UNAVAILABLE_RECEPTIVITY_SIGNALS


def appears_minimizing(message: str) -> bool:
    text = (message or "").strip().casefold()
    if not text:
        return False
    words = _word_count(text)
    if words <= 8 and _contains_any(text, _CLOSED_MARKERS):
        return True
    return False


def _journal_present(journal_context: str) -> bool:
    body = journal_context or ""
    return _JOURNAL_PRESENT in body and "No journal entries" not in body


def assess_receptivity(
    message: str,
    *,
    opening_turn: bool = False,
    journal_context: str = "",
    message_history: Sequence[dict] | None = None,
    extra_features: Iterable[str] | None = None,
) -> ReceptivityState:
    """Descriptive, non-diagnostic. Time-of-day never proves emotion."""
    if extra_features:
        reject_engagement_features(extra_features)

    text = (message or "").strip().casefold()
    if opening_turn and not text:
        return ReceptivityState.NEUTRAL if _journal_present(journal_context) else ReceptivityState.UNKNOWN

    if _contains_any(text, _OVERLOAD_MARKERS):
        return ReceptivityState.HIGH_OVERLOAD

    words = _word_count(text)
    history = list(message_history or [])
    prior_user = sum(1 for item in history if item.get("role") == "user")
    journal = _journal_present(journal_context)
    open_share = _contains_any(text, _OPEN_MARKERS) or journal
    closed = appears_minimizing(text)

    # Current clock bucket is available (APM already uses it). It may increase
    # caution at late night; it must not independently mark the user distressed.
    bucket = temporal_bucket()
    late_night = bucket == "LATE_NIGHT"

    if closed and words <= 6:
        return ReceptivityState.LOW_RECEPTIVITY
    if words <= 3 and not open_share:
        return ReceptivityState.LOW_RECEPTIVITY
    if late_night and words <= 8 and not open_share:
        return ReceptivityState.LOW_RECEPTIVITY
    if open_share and words >= 12:
        return ReceptivityState.RECEPTIVE
    if journal and opening_turn:
        return ReceptivityState.RECEPTIVE
    if prior_user >= 1 and open_share:
        return ReceptivityState.RECEPTIVE
    if opening_turn:
        return ReceptivityState.NEUTRAL
    if words >= 8:
        return ReceptivityState.NEUTRAL
    return ReceptivityState.UNKNOWN


def permits_proactive(state: ReceptivityState) -> bool:
    return state in {ReceptivityState.RECEPTIVE, ReceptivityState.NEUTRAL}

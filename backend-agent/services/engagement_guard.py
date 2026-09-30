"""Engagement and keystroke signals are not psychological evidence."""

from __future__ import annotations

from typing import Iterable, Mapping

FORBIDDEN_FEATURES = frozenset(
    {
        "session_duration",
        "message_count",
        "return_frequency",
        "streak_length",
        "notifications_received",
        "app_open_count",
        "typing_speed",
        "typing_rhythm",
        "typing_pauses",
        "pause_metrics",
        "keystrokes",
        "vocabulary_score",
    }
)


def reject_engagement_features(payload: Mapping[str, object] | Iterable[str]) -> None:
    if isinstance(payload, Mapping):
        keys = set(payload)
    else:
        keys = set(payload)
    found = sorted(keys & FORBIDDEN_FEATURES)
    if found:
        raise ValueError(
            "engagement features are not psychological evidence: " + ", ".join(found)
        )

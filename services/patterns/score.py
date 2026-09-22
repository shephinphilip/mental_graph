"""Confidence, status lifecycle, and time decay for patterns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Tuple

from config import get_settings
from schemas import PatternStatus


def classify_status(evidence_count: int, *, inactive: bool = False) -> PatternStatus:
    settings = get_settings()
    if inactive:
        return PatternStatus.INACTIVE
    if evidence_count >= settings.PATTERN_ESTABLISHED_MIN_EVIDENCE:
        return PatternStatus.ESTABLISHED
    if evidence_count >= settings.PATTERN_EMERGING_MIN_EVIDENCE:
        return PatternStatus.EMERGING
    return PatternStatus.OBSERVATION


def compute_confidence(
    *,
    evidence_count: int,
    contradiction_count: int = 0,
    confirm_count: int = 0,
    disagree_count: int = 0,
    consistency: float = 1.0,
    days_since_last: float = 0.0,
    data_quality: float = 1.0,
) -> float:
    """
    Bounded confidence in [0, 1].

    Factors (documented thresholds live in Settings):
    - evidence volume (saturating)
    - consistency of co-occurrence
    - recency decay
    - contradictions and explicit disagreement (stronger than passive signals)
    - confirmations
    - data quality
    """
    settings = get_settings()
    established = max(1, settings.PATTERN_ESTABLISHED_MIN_EVIDENCE)
    volume = min(1.0, evidence_count / float(established))
    base = 0.25 * volume + 0.35 * max(0.0, min(1.0, consistency)) + 0.2 * data_quality

    base += settings.PATTERN_FEEDBACK_CONFIRM_BOOST * min(confirm_count, 5)
    base -= settings.PATTERN_FEEDBACK_DISAGREE_PENALTY * disagree_count
    base -= settings.PATTERN_CONTRADICTION_PENALTY * contradiction_count

    decay = settings.PATTERN_DECAY_PER_DAY * max(0.0, days_since_last)
    score = base - decay
    return max(0.0, min(1.0, round(score, 4)))


def apply_time_decay(
    confidence: float,
    last_observed_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> Tuple[float, bool]:
    """Return (decayed_confidence, should_mark_inactive)."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    if not last_observed_at:
        return confidence, False
    if last_observed_at.tzinfo is None:
        last_observed_at = last_observed_at.replace(tzinfo=timezone.utc)
    days = max(0.0, (now - last_observed_at).total_seconds() / 86400.0)
    decayed = max(0.0, confidence - settings.PATTERN_DECAY_PER_DAY * days)
    inactive = days >= settings.PATTERN_INACTIVE_DAYS or decayed < 0.15
    return round(decayed, 4), inactive


def strength_from_evidence(evidence_count: int, consistency: float) -> float:
    settings = get_settings()
    est = max(1, settings.PATTERN_ESTABLISHED_MIN_EVIDENCE)
    return round(min(1.0, (evidence_count / float(est)) * max(0.0, min(1.0, consistency))), 4)

"""
Dynamic 1–10 risk intensity scorer.

Zero-LLM, fast-path safe. Used by Inner Council, the sliding-window
pattern engine, and background extraction. Acute crisis keywords still
belong to the existing crisis protocol — this module only scores.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable

from services.apm import contains_crisis_signal

_SOFT_DISTRESS = (
    "hurt",
    "lonely",
    "scared",
    "ashamed",
    "guilty",
    "empty",
    "worthless",
    "sad",
    "anxious",
    "anxiety",
    "stressed",
    "overwhelmed",
    "panic",
    "crying",
)

_HEAVY_DISTRESS = (
    "hopeless",
    "worthless",
    "hate myself",
    "can't go on",
    "cant go on",
    "give up",
    "no point",
    "nothing matters",
    "can't take it",
    "cant take it",
    "breaking down",
    "unbearable",
)

_AROUSAL_MARKERS = (
    "can't sleep",
    "cant sleep",
    "can't breathe",
    "cant breathe",
    "racing",
    "manic",
    "wired",
    "exploding",
    "screaming",
    "shaking",
)

_POSITIVE = (
    "okay",
    "better",
    "grateful",
    "fine",
    "good",
    "relieved",
    "hopeful",
    "calm",
)


@dataclass(frozen=True)
class RiskScore:
    risk_intensity_score: float
    valence: float
    arousal: float
    confidence_score: float
    crisis_keywords: bool
    band: str  # typical | elevated | high | crisis

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(m in text for m in markers)


def _count_hits(text: str, markers: Iterable[str]) -> int:
    return sum(1 for m in markers if m in text)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_turn(user_message: str) -> RiskScore:
    """
    Score one user turn on a 1.0–10.0 risk intensity scale.

    1–7: ordinary fluctuation / manageable distress
    8–10: severe hopelessness, extreme volatility, or crisis language
    """
    text = (user_message or "").strip().lower()
    if not text:
        return RiskScore(1.0, 0.0, 0.1, 0.2, False, "typical")

    crisis = contains_crisis_signal(text)
    soft = _count_hits(text, _SOFT_DISTRESS)
    heavy = _count_hits(text, _HEAVY_DISTRESS)
    arousal_hits = _count_hits(text, _AROUSAL_MARKERS)
    positive = _count_hits(text, _POSITIVE)

    bangs = text.count("!")
    caps_ratio = (
        sum(1 for c in user_message if c.isupper()) / max(len(user_message), 1)
    )
    fragmented = text.count("...") + text.count("…")
    instability = min(2.0, 0.4 * bangs + (1.2 if caps_ratio > 0.45 else 0.0) + 0.3 * fragmented)

    score = 2.0
    score += min(2.4, 0.8 * soft)
    score += min(4.0, 1.8 * heavy)
    score += min(1.6, 0.8 * arousal_hits)
    score += instability
    if heavy >= 2:
        score = max(score, 8.2)
    if heavy >= 3:
        score = max(score, 8.8)
    if positive and not heavy and not crisis:
        score -= min(1.5, 0.5 * positive)

    if crisis:
        score = max(score, 9.0)
        if any(k in text for k in ("suicide", "kill myself", "end my life", "overdose")):
            score = max(score, 9.8)

    score = round(_clamp(score, 1.0, 10.0), 1)

    valence = _clamp(-0.15 * soft - 0.35 * heavy - (0.6 if crisis else 0.0) + 0.2 * positive, -1.0, 1.0)
    arousal = _clamp(0.15 + 0.12 * arousal_hits + 0.08 * bangs + 0.2 * (1 if crisis else 0), 0.0, 1.0)
    markers = soft + heavy + arousal_hits + (2 if crisis else 0)
    confidence = _clamp(0.35 + 0.12 * markers, 0.2, 0.95)

    if crisis or score >= 8.0:
        band = "crisis" if crisis else "high"
    elif score >= 5.0:
        band = "elevated"
    else:
        band = "typical"

    return RiskScore(
        risk_intensity_score=score,
        valence=round(valence, 2),
        arousal=round(arousal, 2),
        confidence_score=round(confidence, 2),
        crisis_keywords=crisis,
        band=band,
    )

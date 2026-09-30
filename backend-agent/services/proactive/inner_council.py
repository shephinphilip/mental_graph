"""Deterministic Inner Council review for a proactive candidate.

Reuses the existing chat council for risk/empathy/reality stance. Does not
spin up extra LLM agents.
"""

from __future__ import annotations

from typing import Optional, Sequence

from services.inner_council import CouncilStance, deliberate
from services.proactive.schemas import CouncilDecision, QuestionCandidate

_CLINICAL = (
    "diagnos",
    "you have anxiety",
    "you have depression",
    "you are depressed",
    "you're depressed",
    "you are anxious",
    "clinical",
    "psychiatrist",
    "cognitive dissonance",
    "as your therapist",
)
_INTERROGATION = (
    "why did you",
    "why would you",
    "what happened?",
    "how are you feeling?",
)
_PRESSURE = (
    "you should",
    "you need to",
    "you must",
    "make sure you",
    "have you tried",
)
_SURVEILLANCE = (
    "typing",
    "opened the app",
    "late at night",
    "your graph",
    "confidence score",
    "telemetry",
    "i saw that you",
    "keystroke",
)
_CERTAIN_INFERENCE = (
    "you are actually",
    "you're actually",
    "this means you",
    "you are clearly",
    "you're clearly",
    "i know you feel",
)


def review_candidate(
    candidate: QuestionCandidate,
    user_message: str,
    message_history: Sequence[dict] | None = None,
    *,
    opening_turn: bool = False,
    council: Optional[CouncilStance] = None,
    risk_intensity: float = 1.0,
    persistent_distress: bool = False,
) -> CouncilDecision:
    stance = council or deliberate(
        user_message,
        message_history,
        opening_turn=opening_turn,
        risk_intensity_score=risk_intensity,
        persistent_distress=persistent_distress,
    )
    question = candidate.question or ""
    lowered = question.casefold()

    if stance.risk_band == "crisis_adjacent" or persistent_distress:
        return CouncilDecision(
            allowed=False,
            reason="risk_blocks_casual_question",
            question="",
            risk_check="suppress",
            empathy_check="skipped",
            reality_check="skipped",
            actionability_check="skipped",
        )
    risk_check = "allow"
    if stance.risk_band == "elevated" and candidate.confidence >= 0.8:
        risk_check = "caution"
        # Elevated distress still allows a low-friction question, not a probe.

    empathy_fail = any(term in lowered for term in _CLINICAL + _INTERROGATION)
    empathy_check = "reject_clinical_or_interrogative" if empathy_fail else "warm"

    reality_fail = any(term in lowered for term in _SURVEILLANCE + _CERTAIN_INFERENCE)
    if candidate.confidence < 0.7 and not any(
        marker in lowered
        for marker in (
            "i wonder",
            "correct me",
            "maybe",
            "could be wrong",
            "a bit",
            "a little",
            "has that felt",
            "how's that",
            "how is that",
            "still around",
            "sitting with you",
        )
    ):
        reality_fail = True
    reality_check = "uncertain_stated_as_fact" if reality_fail else "grounded"

    action_fail = any(term in lowered for term in _PRESSURE)
    actionability_check = (
        "pressure_to_act" if action_fail else "low_friction_question"
    )

    allowed = not (empathy_fail or reality_fail or action_fail)
    reason = "approved" if allowed else (
        empathy_check if empathy_fail else reality_check if reality_fail else actionability_check
    )
    return CouncilDecision(
        allowed=allowed,
        reason=reason,
        question=question if allowed else "",
        risk_check=risk_check,
        empathy_check=empathy_check,
        reality_check=reality_check,
        actionability_check=actionability_check,
    )

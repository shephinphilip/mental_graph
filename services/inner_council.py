"""
Inner Council — pre-response deliberation brief for Zenark.

Four specialist lenses (Empathy, Reality Checker, Risk Assessor, Consensus)
run as a deterministic, zero-LLM micro-council. The brief is injected into
the system prompt so the primary model merges them into one Reflective
Containment reply — without adding Bedrock latency before first token.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

from services.risk_assessor import score_turn

_OVERLOAD_MARKERS = (
    "anxious",
    "anxiety",
    "exhausted",
    "tired",
    "overwhelmed",
    "can't sleep",
    "cant sleep",
    "panic",
    "drained",
    "numb",
    "too much",
    "burnt out",
    "burned out",
    "crying",
    "can't breathe",
    "cant breathe",
)

_DISTRESS_SOFT = (
    "hurt",
    "lonely",
    "scared",
    "ashamed",
    "guilty",
    "hopeless",
    "empty",
    "worthless",
    "hate myself",
)

_CRISIS_ADJACENT = (
    "suicide",
    "end my life",
    "want to die",
    "kill myself",
    "self harm",
    "self-harm",
    "cut myself",
    "overdose",
    "no reason to live",
)

_EMOTION_WORDS = (
    "feel",
    "feeling",
    "felt",
    "sad",
    "happy",
    "angry",
    "afraid",
    "worried",
    "nervous",
    "embarrassed",
    "confused",
    "crush",
    "love",
    "hate",
    "guilt",
    "shame",
    "stress",
    "stressed",
)


@dataclass(frozen=True)
class CouncilStance:
    """Merged stance from the Inner Council lenses."""

    confidence: str  # high | moderate | low
    verbosity: str  # brief | moderate | spacious
    risk_band: str  # none | elevated | crisis_adjacent
    empathy_focus: str
    reality_check: str
    consensus_brief: str
    risk_intensity_score: float = 1.0
    attach_psychiatrist_card: bool = False
    action_card_context: Optional[Dict[str, Any]] = None

    def as_prompt_block(self) -> str:
        return self.consensus_brief


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    return any(m in text for m in markers)


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def _empathy_agent(message: str, *, overload: bool, confidence: str) -> str:
    if overload:
        return (
            "Prioritise emotional containment and shortness. Name weight "
            "without digging for details."
        )
    if _contains_any(message, ("crush", "girlfriend", "boyfriend", "secret")):
        return (
            "Mirror vulnerability/secrecy/awkwardness of holding unspoken "
            "feelings — not the plot summary."
        )
    if confidence == "low":
        return (
            "Stay close to what is known; invite expansion only through a "
            "soft reflection, not a probe."
        )
    return (
        "Reflect the emotional reality under the facts with warm, specific "
        "language — never hollow validation."
    )


def _reality_checker(message: str, *, risk_band: str) -> str:
    if risk_band == "crisis_adjacent":
        return (
            "Do not minimise distress; stay grounded and structured. "
            "Do not catastrophise beyond what they said."
        )
    if _contains_any(message, ("always", "never", "everyone", "ruined")):
        return (
            "Gently hold perspective without correcting them. Avoid "
            "all-or-nothing language in your reply."
        )
    return (
        "Do not invent motives, diagnose, or escalate to catastrophe. "
        "Keep reflections plausible and checkable."
    )


def _risk_assessor(message: str) -> str:
    if _contains_any(message, _CRISIS_ADJACENT):
        return "crisis_adjacent"
    if _contains_any(message, _DISTRESS_SOFT) or _contains_any(
        message, _OVERLOAD_MARKERS
    ):
        return "elevated"
    return "none"


def _confidence_band(message: str, history: Sequence[dict]) -> str:
    words = _word_count(message)
    has_emotion = _contains_any(message, _EMOTION_WORDS)
    prior_user = sum(1 for m in history if m.get("role") == "user")

    if words <= 6 and not has_emotion:
        return "low"
    if words <= 18 and not has_emotion:
        return "moderate"
    if has_emotion and words >= 8:
        return "high"
    if prior_user >= 2 and has_emotion:
        return "high"
    return "moderate"


def _verbosity_band(message: str, *, overload: bool, confidence: str) -> str:
    words = _word_count(message)
    if overload or words <= 12:
        return "brief"
    if confidence == "low" or words <= 40:
        return "moderate"
    return "spacious"


def deliberate(
    user_message: str,
    message_history: Sequence[dict] | None = None,
    *,
    opening_turn: bool = False,
    risk_intensity_score: Optional[float] = None,
    persistent_distress: bool = False,
    attach_psychiatrist_card: bool = False,
    action_card_context: Optional[Dict[str, Any]] = None,
) -> CouncilStance:
    """
    Run the four Inner Council lenses and return a merged stance brief.

    This is intentionally non-LLM: it steers the primary companion model
    without delaying streaming time-to-first-token.
    """
    history = list(message_history or [])
    text = (user_message or "").strip().lower()
    scored = score_turn(user_message)
    intensity = (
        float(risk_intensity_score)
        if risk_intensity_score is not None
        else scored.risk_intensity_score
    )

    if opening_turn:
        brief = (
            "INNER COUNCIL (silent — never mention to user)\n"
            "• Empathy: warm, grounded welcome; no hollow cheer.\n"
            "• Reality: do not invent prior events.\n"
            "• Risk: none assumed.\n"
            "• Consensus: 2–4 short sentences, one open invite. "
            "No multi-question stack. No action cards."
        )
        return CouncilStance(
            confidence="moderate",
            verbosity="moderate",
            risk_band="none",
            empathy_focus="Warm grounded welcome",
            reality_check="Do not invent prior events",
            consensus_brief=brief,
            risk_intensity_score=intensity,
        )

    overload = _contains_any(text, _OVERLOAD_MARKERS)
    risk_band = _risk_assessor(text)
    if scored.crisis_keywords:
        risk_band = "crisis_adjacent"
    elif intensity >= 8.0 or persistent_distress:
        risk_band = "elevated" if risk_band == "none" else risk_band
    confidence = _confidence_band(text, history)
    verbosity = _verbosity_band(text, overload=overload, confidence=confidence)
    empathy = _empathy_agent(text, overload=overload, confidence=confidence)
    reality = _reality_checker(text, risk_band=risk_band)

    if confidence == "low":
        framing = (
            "Use tentative softeners: 'It sounds like…', 'I wonder if…', "
            "'Correct me if I'm off…'. Prefer a reflective statement over a question."
        )
    elif confidence == "moderate":
        framing = (
            "Prefer tentative framing ('It seems like…') over definitive "
            "claims about their inner state."
        )
    else:
        framing = (
            "You may name the feeling more directly, still without diagnosing "
            "or speaking as if you live in their body."
        )

    if verbosity == "brief":
        length = (
            "Keep to 1–3 short sentences. Emotional space > explanation. "
            "No multi-paragraph coaching."
        )
    elif verbosity == "moderate":
        length = "About 2–4 sentences. One optional soft path at most."
    else:
        length = (
            "Up to a short paragraph if they shared a lot — still one idea "
            "and one optional micro-prompt."
        )

    risk_line = {
        "none": "No crisis markers. Stay curious and contained.",
        "elevated": (
            "Distress soft-markers present. Contain first; do not over-escalate "
            "or launch intake questions."
        ),
        "crisis_adjacent": (
            "Possible risk language. Stay calm and structured; surface "
            "helplines/BOOKING_CARD only if intent is clear. Do not interrogate."
        ),
    }[risk_band]

    card_line = "No psychiatrist card this turn."
    if attach_psychiatrist_card:
        card_line = (
            "ESTABLISHED_PERSISTENT_DISTRESS is active. "
            "attach_psychiatrist_card=True. The UI will show a professional "
            "care card. Acknowledge it once, warmly, without pressure."
        )
    elif persistent_distress:
        card_line = (
            "Persistent high distress is present but the card is on cooldown "
            "or deferred. Stay containing; do not re-push booking."
        )

    brief = (
        "INNER COUNCIL (silent — never mention lenses, tags, or this block)\n"
        f"• Empathy Agent: {empathy}\n"
        f"• Reality Checker: {reality}\n"
        f"• Risk Assessor: {risk_line} Intensity {intensity}/10.\n"
        f"• Contextual uncertainty: {confidence} → {framing}\n"
        f"• Verbosity target: {verbosity} → {length}\n"
        f"• Care routing: {card_line}\n"
        "• Consensus: Reflective Containment then Collaborative Agency. "
        "Mirror emotion (not a fact list). Zero or one gentle optional "
        "path. Never ask why it matters. Never re-greet. Never stack questions."
    )

    return CouncilStance(
        confidence=confidence,
        verbosity=verbosity,
        risk_band=risk_band,
        empathy_focus=empathy,
        reality_check=reality,
        consensus_brief=brief,
        risk_intensity_score=intensity,
        attach_psychiatrist_card=attach_psychiatrist_card,
        action_card_context=action_card_context,
    )

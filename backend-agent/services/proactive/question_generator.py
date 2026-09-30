"""Deterministic Mirror-With-Agency question candidates. No extra LLM call."""

from __future__ import annotations

from typing import Optional

from services.proactive.schemas import QuestionCandidate, ReceptivityState
from services.proactive.trigger_engine import GraphSnippet


def _age_band(age: Optional[int]) -> str:
    if age is None:
        return "unknown"
    try:
        value = int(age)
    except (TypeError, ValueError):
        return "unknown"
    if 5 <= value <= 9:
        return "young"
    if 10 <= value <= 13:
        return "middle"
    if 14 <= value <= 17:
        return "teen"
    return "unknown"


def _topic_phrase(label: str) -> str:
    topic = " ".join((label or "").split())
    if not topic:
        return "that situation"
    lowered = topic.casefold()
    if lowered.startswith("the "):
        return topic
    if " " in topic or any(ch.isupper() for ch in topic[1:]):
        return topic
    return topic


def generate_candidate(
    *,
    trigger_type: str,
    topic_node: GraphSnippet,
    receptivity: ReceptivityState,
    confidence: float,
    language: str,
    script: str,
    risk_state: str,
    age: Optional[int] = None,
    opening_turn: bool = False,
    reason: str = "",
) -> Optional[QuestionCandidate]:
    topic = _topic_phrase(topic_node.label)
    if not trigger_type or topic == "that situation" and not topic_node.label:
        return None
    band = _age_band(age)
    tentative = confidence < 0.7 or receptivity != ReceptivityState.RECEPTIVE
    question = _compose(
        trigger_type,
        topic,
        band=band,
        tentative=tentative,
        opening_turn=opening_turn,
    )
    if not question:
        return None
    return QuestionCandidate(
        question=question,
        reason=reason or trigger_type.lower(),
        trigger_type=trigger_type,
        source_nodes=[topic_node.node_id] if topic_node.node_id else [],
        source_labels=[topic] if topic_node.label else [],
        confidence=round(float(confidence), 3),
        receptivity=receptivity.value,
        risk_state=risk_state,
        language=language or "ENGLISH",
        script=script or "LATIN",
        topic=topic,
    )


def _compose(
    trigger_type: str,
    topic: str,
    *,
    band: str,
    tentative: bool,
    opening_turn: bool,
) -> str:
    if band == "young":
        return _young(trigger_type, topic, tentative=tentative)
    if band == "middle":
        return _middle(trigger_type, topic, tentative=tentative)
    return _teen(trigger_type, topic, tentative=tentative, opening_turn=opening_turn)


def _young(trigger_type: str, topic: str, *, tentative: bool) -> str:
    if trigger_type == "GRAPH_DIVERGENCE":
        if tentative:
            return (
                f"I could be wrong, but I keep thinking about {topic}. "
                "Has that felt any easier, or is it still around?"
            )
        return (
            f"You mentioned {topic} before. "
            "Has that felt any easier?"
        )
    if trigger_type == "JOURNAL_CONTEXT":
        return (
            "I remember you wrote something down recently. "
            "Has that been sitting with you today, or does it feel lighter?"
        )
    if trigger_type in {"SLEEP_CONTEXT"}:
        return (
            "Sleep sounded a bit messy lately. "
            "Has rest been any easier?"
        )
    return (
        f"I remember {topic} was on your mind. "
        "Has that felt any easier?"
    )


def _middle(trigger_type: str, topic: str, *, tentative: bool) -> str:
    if trigger_type == "GRAPH_DIVERGENCE":
        lead = (
            "Correct me if I'm off, but it sounded like "
            if tentative
            else "I remember "
        )
        return (
            f"{lead}{topic} was still in the mix. "
            "Has it eased up a little, or is it still sitting there?"
        )
    if trigger_type in {"RECOVERY_CHECK", "OUTCOME_FOLLOW_UP"}:
        return (
            f"Last time, {topic} was something you were working through. "
            "Has that felt any more manageable, or is it about the same?"
        )
    if trigger_type == "JOURNAL_CONTEXT":
        return (
            "Something you wrote down recently is still in the back of my mind. "
            "Has that eased up at all?"
        )
    return (
        f"I remember {topic} was something you were carrying. "
        "How's that been feeling — a bit lighter, or still around?"
    )


def _teen(
    trigger_type: str,
    topic: str,
    *,
    tentative: bool,
    opening_turn: bool,
) -> str:
    if trigger_type == "GRAPH_DIVERGENCE":
        if tentative:
            return (
                f"Correct me if I'm off, but {topic} seemed to be sitting with you. "
                "Has it eased up a little, or is it still in the back of your mind?"
            )
        return (
            f"I remember {topic} was weighing on you a bit. "
            "How has that been feeling lately — manageable, or still sitting there?"
        )
    if trigger_type in {"RECOVERY_CHECK", "OUTCOME_FOLLOW_UP"}:
        return (
            f"I remember {topic} was something you were carrying. "
            "Has it felt any more workable, or has it been about the same?"
        )
    if trigger_type == "ACADEMIC_STRESS_CONTEXT":
        return (
            f"I remember {topic} was on your plate. "
            "How's that sitting with you now — a bit lighter, or still around?"
        )
    if trigger_type == "SLEEP_CONTEXT":
        return (
            "Rest sounded like it had been a bit off. "
            "Has sleep been any easier, or still uneven?"
        )
    if trigger_type == "JOURNAL_CONTEXT":
        return (
            "I keep thinking about something you wrote down recently. "
            "Has that eased up at all?"
        )
    if trigger_type == "HABIT_CONTEXT":
        return (
            "That small routine you were trying has been on my mind. "
            "Has it been feeling doable, or more like a stretch?"
        )
    if opening_turn or trigger_type == "FOLLOW_UP_ON_PREVIOUS_CONTEXT":
        return (
            f"I remember {topic} was weighing on you a bit. "
            "How did it end up feeling — eased up a little, or still sitting with you?"
        )
    if trigger_type == "PATTERN_CLARIFICATION":
        if tentative:
            return (
                f"Maybe I'm reading too much into it, but {topic} still seems present. "
                "Is it more in the background, or still taking up space?"
            )
        return (
            f"I remember {topic} was something you were carrying this week. "
            "I wonder how it feels now — has it eased up a little, or is it still on your mind?"
        )
    return (
        f"I remember {topic} was something you were carrying. "
        "Has that situation eased up a little, or is it still sitting with you?"
    )


def soften_once(question: str) -> str:
    """Bounded rewrite: keep a single question and add tentative framing."""
    body = (question or "").strip()
    if not body:
        return body
    if body.count("?") > 1:
        head, _sep, _rest = body.partition("?")
        body = head.strip() + "?"
    lowered = body.casefold()
    if lowered.startswith(
        ("i wonder", "correct me", "maybe i'm", "maybe i am", "i could be wrong")
    ):
        return body
    if body.count("?") == 0:
        body = body.rstrip(".") + "?"
    first = body[0].lower() + body[1:] if body else body
    return f"I wonder — {first}"

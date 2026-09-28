"""Keep only stable learning facts. A one-off question is not a memory."""

from __future__ import annotations

import re

from services.apm import contains_crisis_signal

from exam_buddy_guardrails.models import MemoryCandidate, NodeType, RelationType

_PREFERENCE = re.compile(
    r"\b(always understand|understand .+ better when|prefer|i like it when you show the steps|"
    r"show the steps|step by step)\b",
    re.IGNORECASE,
)
_STRUGGLE = re.compile(
    r"\b(?:keep making mistakes|always make mistakes|struggle with|struggling with|"
    r"keep getting .+ wrong)\b(?:\s+(?:when|while|in|with|on))?(?:\s+(?:solving|doing|using))?\s+(.+)",
    re.IGNORECASE,
)
_MISTAKE = re.compile(
    r"\b(?:mistake|mistakes|errors?) (?:when|in|while) (?:applying|using) (.+)",
    re.IGNORECASE,
)
_MASTERED = re.compile(
    r"\b(?:finally understand|now understand|i understand|i've mastered|i have mastered)\s+(.+)",
    re.IGNORECASE,
)
_EXAM = re.compile(
    r"\bpreparing for (?:the |my )?(.+? exam)\b",
    re.IGNORECASE,
)
_GOAL = re.compile(
    r"\b(?:my goal is to|i want to learn|i want to finish)\s+(.+)",
    re.IGNORECASE,
)
_BLOCKED = {
    "anxiety", "lonely", "loneliness", "suicide", "sad", "depression", "crisis",
}


def _clean_capture(value: str) -> str:
    return value.strip(" .!?,;:\"'")


def extract_memories(text: str) -> list[MemoryCandidate]:
    """Return candidate facts. Crisis text yields nothing."""
    raw = (text or "").strip()
    if not raw or contains_crisis_signal(raw):
        return []

    found: list[MemoryCandidate] = []
    if _PREFERENCE.search(raw) and re.search(r"\b(always|prefer|better when|i like)\b", raw, re.I):
        found.append(
            MemoryCandidate(
                relation=RelationType.PREFERS,
                node_type=NodeType.LEARNING_PREFERENCE,
                label="step-by-step explanations",
            )
        )

    struggle = _STRUGGLE.search(raw)
    if struggle:
        label = _clean_capture(struggle.group(1))
        if label.casefold() not in _BLOCKED:
            found.append(
                MemoryCandidate(
                    relation=RelationType.STRUGGLES_WITH,
                    node_type=NodeType.TOPIC,
                    label=label,
                )
            )

    mistake = _MISTAKE.search(raw)
    if mistake:
        found.append(
            MemoryCandidate(
                relation=RelationType.MADE_MISTAKE_IN,
                node_type=NodeType.CONCEPT,
                label=_clean_capture(mistake.group(1)),
            )
        )

    mastered = _MASTERED.search(raw)
    if mastered and re.search(r"\b(finally|mastered|now understand)\b", raw, re.I):
        found.append(
            MemoryCandidate(
                relation=RelationType.MASTERED,
                node_type=NodeType.TOPIC,
                label=_clean_capture(mastered.group(1)),
            )
        )

    exam = _EXAM.search(raw)
    if exam:
        found.append(
            MemoryCandidate(
                relation=RelationType.PREPARING_FOR,
                node_type=NodeType.EXAM,
                label=_clean_capture(exam.group(1)),
            )
        )

    goal = _GOAL.search(raw)
    if goal:
        found.append(
            MemoryCandidate(
                relation=RelationType.HAS_GOAL,
                node_type=NodeType.STUDY_GOAL,
                label=_clean_capture(goal.group(1)),
            )
        )
    return found

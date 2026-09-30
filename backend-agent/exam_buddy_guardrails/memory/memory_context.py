"""Short sentences for the prompt. Not a dump of the graph."""

from __future__ import annotations

from exam_buddy_guardrails.models import RetrievedMemory

_LINES = {
    "STRUGGLES_WITH": "The student has previously struggled with {label}.",
    "MASTERED": "The student has previously mastered {label}.",
    "PREFERS": "The student prefers {label}.",
    "MADE_MISTAKE_IN": "The student previously made errors when applying {label}.",
    "PREPARING_FOR": "The student is preparing for {label}.",
    "HAS_GOAL": "The student has a study goal: {label}.",
    "STUDIES": "The student studies {label}.",
    "LEARNING": "The student has been learning {label}.",
    "DISCUSSED": "A previous conversation touched on {label}.",
    "RELATED_TO": "A related learning note is {label}.",
    "REQUIRES_REVIEW": "The student still needs review on {label}.",
}


def render_memory_lines(memories: list[RetrievedMemory]) -> list[str]:
    lines: list[str] = []
    for memory in memories:
        template = _LINES.get(memory.relation)
        if not template or not memory.label:
            continue
        lines.append(template.format(label=memory.label))
    return lines

"""How retrieved learning notes are shown to the model."""

from __future__ import annotations


def memory_context_block(lines: list[str]) -> str:
    if not lines:
        return ""
    bullets = "\n".join(f"- {line}" for line in lines)
    return (
        "STUDENT MEMORY CONTEXT\n"
        "The following information was retrieved from the student's personal\n"
        "learning graph.\n"
        "Use it only when relevant to the current question.\n"
        "It is user-specific context, not authoritative academic knowledge.\n"
        "Do not invent memories that are not listed.\n"
        "Do not mention the existence of the memory graph unless necessary.\n"
        "Do not reveal internal memory implementation details.\n\n"
        f"{bullets}"
    )

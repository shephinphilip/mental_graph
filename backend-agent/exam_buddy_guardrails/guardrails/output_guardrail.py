"""Last gate. Removes prompt leaks and dated memory disclosures."""

from __future__ import annotations

import re

_LEAK = re.compile(
    r"\b(system prompt|student memory context|node_type|graph_nodes|"
    r"exam_buddy_nodes|memory graph|internal memory)\b",
    re.IGNORECASE,
)
_DATED_MEMORY = re.compile(
    r"you previously told me on [a-z]+ \d{1,2}",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def apply_output_guardrail(text: str) -> str:
    """Keep the academic answer. Drop lines that expose the machinery."""
    cleaned = _DATED_MEMORY.sub("Let's take this one step at a time", text or "")
    kept: list[str] = []
    for match in _SENTENCE.finditer(cleaned):
        sentence = match.group(0).strip()
        if not sentence or _LEAK.search(sentence):
            continue
        kept.append(sentence)
    reply = " ".join(kept).strip()
    if not reply:
        return "Let's work through the problem directly."
    return reply

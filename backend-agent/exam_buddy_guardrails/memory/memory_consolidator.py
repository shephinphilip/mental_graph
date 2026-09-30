"""Validate, dedupe, and write learning facts. Never writes crisis text."""

from __future__ import annotations

from services.apm import contains_crisis_signal

from exam_buddy_guardrails.memory.graph_repository import canonical_label, upsert_fact
from exam_buddy_guardrails.memory.memory_extractor import extract_memories


async def consolidate_learning_memory(db, user_id: str, message: str) -> int:
    """Store new stable facts for one user. Returns how many were accepted."""
    if contains_crisis_signal(message):
        return 0
    written = 0
    seen: set[tuple[str, str]] = set()
    for candidate in extract_memories(message):
        key = (candidate.relation.value, canonical_label(candidate.label))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        if await upsert_fact(
            db,
            user_id,
            candidate.relation,
            candidate.node_type,
            candidate.label,
        ):
            written += 1
    return written

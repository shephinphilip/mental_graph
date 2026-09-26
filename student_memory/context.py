"""Fact block for the system prompt."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

EMPTY = "No stored facts yet."


def _band(score: float) -> str:
    if score >= 0.7:
        return "strong"
    if score >= 0.4:
        return "moderate"
    return "faint"


def format_memory_context(facts: List[Dict[str, Any]]) -> str:
    if not facts:
        return EMPTY
    lines = [
        "STUDENT FACTS (from earlier session readings; confirm before building on a faint one):"
    ]
    for fact in facts:
        score = float(fact.get("effective_importance") or fact.get("importance") or 0)
        lines.append(f"- [{fact.get('category', 'OTHER')}] {fact.get('fact')} ({_band(score)})")
    return "\n".join(lines)


async def build_memory_context(db, user_id: str) -> str:
    try:
        from student_memory.store import retrieve_facts

        return format_memory_context(await retrieve_facts(db, user_id))
    except Exception:
        logger.exception("Student memory context failed user=%s", user_id)
        return EMPTY

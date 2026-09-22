"""Bounded relevance retrieval for pattern context injection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from config import get_settings
from services.patterns.score import apply_time_decay
from services.patterns.store import list_active_patterns


_DOMAIN_KEYWORDS = {
    "sleep": {"sleep", "tired", "insomnia", "rest"},
    "academic": {"exam", "marks", "physics", "math", "study", "school", "test", "jee", "neet"},
    "mood": {"stress", "anxious", "sad", "overwhelm", "mood", "feel"},
    "meditation": {"meditat", "breath", "calm", "ground", "cope"},
    "attendance": {"attendance", "class", "missed"},
    "tasks": {"task", "homework", "pending", "deadline"},
    "journaling": {"journal", "wrote", "diary"},
    "conversation": {"talk", "share", "friend"},
}


def _message_domains(message: str) -> Set[str]:
    text = (message or "").lower()
    hits: Set[str] = set()
    for domain, keys in _DOMAIN_KEYWORDS.items():
        if any(k in text for k in keys):
            hits.add(domain)
    return hits


async def retrieve_relevant_patterns(
    db,
    user_id: str,
    user_message: str = "",
    *,
    max_patterns: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Return up to N relevant patterns (possibly zero).

    Applies confidence floor, recency decay, domain overlap, and inactive filter.
    """
    settings = get_settings()
    limit = max_patterns if max_patterns is not None else settings.PATTERN_RETRIEVAL_MAX
    raw = await list_active_patterns(
        db,
        user_id,
        min_confidence=0.0,  # decay applied below
        limit=40,
    )
    topic_domains = _message_domains(user_message)
    now = datetime.now(timezone.utc)
    scored: List[Dict[str, Any]] = []

    for doc in raw:
        decayed, inactive = apply_time_decay(
            float(doc.get("confidence") or 0.0),
            doc.get("last_observed_at"),
            now=now,
        )
        if inactive or doc.get("status") == "INACTIVE":
            continue
        if decayed < settings.PATTERN_RETRIEVAL_MIN_CONFIDENCE:
            continue
        domains = set(doc.get("domains") or [])
        overlap = len(domains & topic_domains) if topic_domains else 0
        # If the user gave a clear topical message with zero overlap, skip;
        # if message is vague, allow high-confidence established patterns through.
        if topic_domains and overlap == 0 and decayed < 0.75:
            continue
        relevance = decayed + 0.05 * overlap
        item = dict(doc)
        item["_retrieval_confidence"] = decayed
        item["_relevance"] = relevance
        scored.append(item)

    scored.sort(key=lambda d: (-d["_relevance"], -d.get("evidence_count", 0)))
    return scored[: max(0, limit)]


def format_pattern_context(patterns: List[Dict[str, Any]]) -> str:
    if not patterns:
        return "No longitudinal user patterns available for this turn."

    blocks = [
        "USER PATTERN CONTEXT (longitudinal — separate from Graph RAG and APM)",
        "Use only if relevant. Do not mention analytics. Do not claim causation or diagnosis.",
        "Invite gentle confirmation when you surface a pattern. Max one pattern reference per turn.",
        "",
    ]
    for idx, pat in enumerate(patterns, start=1):
        conf = pat.get("_retrieval_confidence", pat.get("confidence", 0))
        last = pat.get("last_observed_at")
        if isinstance(last, datetime):
            days = max(0, int((datetime.now(timezone.utc) - (
                last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            )).total_seconds() // 86400))
            last_txt = f"{days} day(s) ago"
        else:
            last_txt = "unknown"
        blocks.extend(
            [
                f"Pattern {idx}:",
                f"  Type: {pat.get('pattern_type')}",
                f"  Description: {pat.get('description')}",
                f"  Evidence count: {pat.get('evidence_count')}",
                f"  Confidence: {conf}",
                f"  Status: {pat.get('status')}",
                f"  Last observed: {last_txt}",
                f"  Domains: {', '.join(pat.get('domains') or [])}",
                f"  Pattern id: {pat.get('pattern_id')}",
                "  Interpretation: Tentative relationship, not proof of causation.",
                "",
            ]
        )
    return "\n".join(blocks).strip()

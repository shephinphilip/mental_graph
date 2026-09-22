"""Explicit user feedback on detected patterns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from schemas import PatternFeedbackEvent
from services.patterns.score import classify_status, compute_confidence
from services.patterns.store import (
    PATTERNS_COLLECTION,
    append_evidence,
    get_pattern_by_id,
)


async def record_pattern_feedback(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    pattern_id: str,
    event_type: PatternFeedbackEvent | str,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Record CONFIRM / DISAGREE / NOT_RELATED / HELPFUL / NOT_HELPFUL.

    User disagreement has stronger evidentiary weight than passive metrics.
    Always scoped to authenticated user_id.
    """
    event = (
        event_type
        if isinstance(event_type, PatternFeedbackEvent)
        else PatternFeedbackEvent(str(event_type))
    )
    pattern = await get_pattern_by_id(db, user_id, pattern_id)
    if not pattern:
        raise ValueError("Pattern not found for authenticated user")

    settings = get_settings()
    confirm = int(pattern.get("confirm_count") or 0)
    disagree = int(pattern.get("disagree_count") or 0)
    contradictions = int(pattern.get("contradiction_count") or 0)
    evidence = int(pattern.get("evidence_count") or 0)

    if event in {
        PatternFeedbackEvent.CONFIRM,
        PatternFeedbackEvent.HELPFUL,
        PatternFeedbackEvent.STARTED,
    }:
        confirm += 1
        if event != PatternFeedbackEvent.STARTED:
            evidence += 1
    elif event in {
        PatternFeedbackEvent.DISAGREE,
        PatternFeedbackEvent.NOT_RELATED,
        PatternFeedbackEvent.NOT_HELPFUL,
        PatternFeedbackEvent.DISMISS,
    }:
        disagree += 1
        contradictions += 1

    confidence = compute_confidence(
        evidence_count=evidence,
        contradiction_count=contradictions,
        confirm_count=confirm,
        disagree_count=disagree,
        consistency=max(0.0, 1.0 - (disagree / max(evidence, 1))),
        data_quality=1.0,
    )
    status = classify_status(evidence).value
    if disagree >= confirm + 2 and confidence < settings.PATTERN_RETRIEVAL_MIN_CONFIDENCE:
        status = "INACTIVE"

    now = datetime.now(timezone.utc)
    updates = {
        "confirm_count": confirm,
        "disagree_count": disagree,
        "contradiction_count": contradictions,
        "evidence_count": evidence,
        "confidence": confidence,
        "status": status,
        "updated_at": now,
        "last_feedback_at": now,
        "last_feedback_event": event.value,
    }
    if event in {
        PatternFeedbackEvent.DISMISS,
        PatternFeedbackEvent.NOT_HELPFUL,
        PatternFeedbackEvent.NOT_RELATED,
        PatternFeedbackEvent.DISAGREE,
    }:
        updates["card_dismissed_at"] = now
    await db[PATTERNS_COLLECTION].update_one(
        {"user_id": user_id, "pattern_id": pattern_id},
        {"$set": updates},
    )
    await append_evidence(
        db,
        user_id=user_id,
        pattern_id=pattern_id,
        source="user_feedback",
        feature="feedback",
        value=event.value,
        event_key=f"{user_id}:{pattern_id}:feedback:{event.value}:{int(now.timestamp())}",
        confidence=1.0,
        provenance=note or "explicit_user_feedback",
        event_at=now,
    )
    return {
        "pattern_id": pattern_id,
        "confidence": confidence,
        "status": status,
        "confirm_count": confirm,
        "disagree_count": disagree,
    }

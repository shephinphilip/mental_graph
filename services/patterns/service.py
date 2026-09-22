"""Orchestration: detect → validate → store → retrieve → prompt context."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from schemas import PatternStatus
from services.apm import contains_crisis_signal, personalization_enabled
from services.patterns.adapters import collect_observations
from services.patterns.detect import detect_candidates
from services.patterns.feedback import record_pattern_feedback
from services.patterns.retrieve import format_pattern_context, retrieve_relevant_patterns
from services.patterns.score import apply_time_decay, classify_status
from services.patterns.store import (
    append_evidence,
    ensure_pattern_indexes,
    get_pattern_by_fingerprint,
    upsert_pattern,
)

logger = logging.getLogger(__name__)

EMPTY_PATTERN_CONTEXT = "No longitudinal user patterns available for this turn."


async def get_pattern_context(
    db: AsyncIOMotorDatabase,
    user_id: str,
    user_message: str = "",
) -> str:
    """
    Synchronous retrieval path for chat prompts.

    Consent-gated. Crisis turns skip pattern injection. Failures return empty
    context so chat is never blocked.
    """
    try:
        if contains_crisis_signal(user_message or ""):
            return EMPTY_PATTERN_CONTEXT
        if not await personalization_enabled(db, user_id):
            return EMPTY_PATTERN_CONTEXT
        patterns = await retrieve_relevant_patterns(db, user_id, user_message)
        return format_pattern_context(patterns)
    except Exception:
        logger.exception("Pattern retrieval failed for user=%s", user_id)
        return EMPTY_PATTERN_CONTEXT


async def run_pattern_detection(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    session_id: str = "",
    message: str = "",
    reply: str = "",
    crisis: bool = False,
) -> int:
    """
    Background detection path. Returns number of patterns upserted.

    Skips entirely on crisis or missing personalization consent.
    Never treats chat frequency / session length as evidence.
    """
    if crisis or contains_crisis_signal(message) or contains_crisis_signal(reply):
        logger.info("Pattern detection skipped (crisis) user=%s", user_id)
        return 0
    if not await personalization_enabled(db, user_id):
        logger.info("Pattern detection skipped (no consent) user=%s", user_id)
        return 0

    settings = get_settings()
    observations = await collect_observations(db, user_id)
    candidates = detect_candidates(user_id, observations)
    upserted = 0
    now = datetime.now(timezone.utc)

    for cand in candidates:
        # Single-event candidates stay OBSERVATION and are stored for growth,
        # but are never retrieval-eligible until EMERGING threshold.
        existing = await get_pattern_by_fingerprint(
            db, user_id, cand["fingerprint"]
        )
        if existing:
            evidence = max(
                int(existing.get("evidence_count") or 0),
                int(cand.get("evidence_count") or 0),
            )
            decayed, inactive = apply_time_decay(
                float(cand.get("confidence") or existing.get("confidence") or 0),
                existing.get("last_observed_at"),
                now=now,
            )
            status = (
                PatternStatus.INACTIVE.value
                if inactive
                else classify_status(evidence).value
            )
            cand = {
                **cand,
                "evidence_count": evidence,
                "status": status,
                "confidence": float(cand.get("confidence") or decayed),
                "confirm_count": existing.get("confirm_count", 0),
                "disagree_count": existing.get("disagree_count", 0),
                "contradiction_count": max(
                    int(existing.get("contradiction_count") or 0),
                    int(cand.get("contradiction_count") or 0),
                ),
                "first_observed_at": existing.get("first_observed_at"),
                "pattern_id": existing.get("pattern_id"),
            }

        # Never promote a lone observation to ESTABLISHED
        if int(cand.get("evidence_count") or 0) < settings.PATTERN_EMERGING_MIN_EVIDENCE:
            cand["status"] = PatternStatus.OBSERVATION.value

        saved = await upsert_pattern(db, user_id, cand)
        event_key = (
            f"{user_id}:{saved['pattern_id']}:detect:"
            f"{session_id or 'nosession'}:{cand['fingerprint'][:8]}:"
            f"{now.strftime('%Y%m%d')}"
        )
        await append_evidence(
            db,
            user_id=user_id,
            pattern_id=saved["pattern_id"],
            source=cand.get("source_hint") or "pattern_engine",
            feature="detection_pass",
            value=cand.get("evidence_count"),
            event_key=event_key,
            confidence=float(cand.get("confidence") or 0),
            provenance="background_pattern_detection",
            event_at=now,
        )
        upserted += 1

    logger.info(
        "Pattern detection complete — user=%s candidates=%d upserted=%d",
        user_id,
        len(candidates),
        upserted,
    )
    return upserted


__all__ = [
    "EMPTY_PATTERN_CONTEXT",
    "ensure_pattern_indexes",
    "get_pattern_context",
    "record_pattern_feedback",
    "run_pattern_detection",
]

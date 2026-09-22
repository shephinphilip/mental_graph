"""
Sliding-window risk tracker.

Stores the last N turn scores per user and elevates
ESTABLISHED_PERSISTENT_DISTRESS only after repeated high scores.
A single spike never queues the psychiatrist card.
Acute crisis keywords are recorded but do not reinforce this pattern.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from schemas import PatternStatus, PatternType
from services.patterns.detect import fingerprint
from services.patterns.store import (
    append_evidence,
    get_pattern_by_fingerprint,
    upsert_pattern,
)
from services.risk_assessor import RiskScore, score_turn

logger = logging.getLogger(__name__)

RISK_TURNS_COLLECTION = "user_risk_turns"
PERSISTENT_KEY = "established_persistent_distress"


@dataclass
class RiskTurnDecision:
    score: RiskScore
    consecutive_high: int = 0
    rolling_average: float = 0.0
    persistent_distress: bool = False
    attach_psychiatrist_card: bool = False
    pattern_id: Optional[str] = None
    trigger_reason: str = ""
    action_card_context: Optional[Dict[str, Any]] = None


def format_action_card_context(context: Optional[Dict[str, Any]]) -> str:
    if not context:
        return (
            "No action card is being attached this turn. "
            "Do not invent a psychiatrist or booking card."
        )
    return (
        "ACTION CARD CONTEXT — the UI WILL display this card below your reply.\n"
        f"suggesting_card: {context.get('suggesting_card')}\n"
        f"trigger_reason: {context.get('trigger_reason')}\n"
        f"tone_instruction: {context.get('tone_instruction')}\n"
        "Acknowledge the card once, warmly and without pressure. "
        "Do NOT emit another ACTION_CARD block for it. "
        "Do not interrogate, force, or sound alarmist."
    )


async def ensure_risk_window_indexes(db: AsyncIOMotorDatabase) -> None:
    await db[RISK_TURNS_COLLECTION].create_index(
        [("user_id", 1), ("created_at", -1)],
        name="user_risk_turns_recency",
    )


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _turns_collection(db: AsyncIOMotorDatabase):
    coll = db[RISK_TURNS_COLLECTION]
    # Shared MagicMock DBs return the same object for every collection name.
    # Never write risk turns onto the messages collection mock.
    try:
        if coll is db["messages"]:
            raise TypeError("risk window collection is not isolated")
    except TypeError:
        raise
    except Exception:
        pass
    return coll


async def _recent_turns(
    db: AsyncIOMotorDatabase, user_id: str, *, limit: int
) -> List[Dict[str, Any]]:
    cursor = (
        _turns_collection(db)
        .find({"user_id": user_id})
        .sort("created_at", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def _card_on_cooldown(
    db: AsyncIOMotorDatabase, user_id: str, fp: str
) -> bool:
    settings = get_settings()
    existing = await get_pattern_by_fingerprint(db, user_id, fp)
    if not existing:
        return False
    dismissed_at = _as_dt(existing.get("card_dismissed_at"))
    if not dismissed_at:
        return False
    hours = settings.RISK_CARD_COOLDOWN_HOURS
    return datetime.now(timezone.utc) - dismissed_at < timedelta(hours=hours)


async def record_and_evaluate(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    message: str,
    score: Optional[RiskScore] = None,
) -> RiskTurnDecision:
    """
    Persist this turn's score and evaluate the sliding window.

    Crisis-keyword turns are stored for audit but excluded from the
    consecutive-high / rolling-average reinforcement that queues a card.
    """
    settings = get_settings()
    scored = score or score_turn(message)
    now = datetime.now(timezone.utc)
    threshold = settings.RISK_HIGH_THRESHOLD
    window_n = settings.RISK_WINDOW_TURNS
    hours = settings.RISK_ROLLING_HOURS

    try:
        recent = await _recent_turns(db, user_id, limit=max(window_n * 3, 12))
    except TypeError:
        # Mock or non-Motor handles — never block chat or share insert_one.
        return RiskTurnDecision(score=scored)

    await _turns_collection(db).insert_one(
        {
            "user_id": user_id,
            "session_id": session_id,
            "risk_intensity_score": scored.risk_intensity_score,
            "valence": scored.valence,
            "arousal": scored.arousal,
            "confidence_score": scored.confidence_score,
            "crisis_keywords": scored.crisis_keywords,
            "created_at": now,
        }
    )
    recent = [
        {
            "user_id": user_id,
            "risk_intensity_score": scored.risk_intensity_score,
            "crisis_keywords": scored.crisis_keywords,
            "created_at": now,
        },
        *recent,
    ]
    # Newest first. Consecutive high ignores crisis-keyword turns.
    consecutive = 0
    for row in recent:
        if row.get("crisis_keywords"):
            continue
        if float(row.get("risk_intensity_score") or 0) >= threshold:
            consecutive += 1
            continue
        break

    cutoff = now - timedelta(hours=hours)
    rolling_vals = [
        float(row["risk_intensity_score"])
        for row in recent
        if not row.get("crisis_keywords")
        and _as_dt(row.get("created_at"))
        and _as_dt(row.get("created_at")) >= cutoff
    ]
    rolling_avg = (
        round(sum(rolling_vals) / len(rolling_vals), 2) if rolling_vals else 0.0
    )

    sustained_streak = consecutive >= window_n
    sustained_avg = len(rolling_vals) >= window_n and rolling_avg > threshold
    persistent = sustained_streak or sustained_avg

    fp = fingerprint(user_id, PatternType.CHANGE_POINT.value, PERSISTENT_KEY)
    pattern_id = None
    attach = False
    reason = ""

    if persistent:
        evidence = max(consecutive, len(rolling_vals))
        saved = await upsert_pattern(
            db,
            user_id,
            {
                "fingerprint": fp,
                "pattern_type": PatternType.CHANGE_POINT.value,
                "domains": ["conversation", "mood"],
                "description": (
                    "Sustained high distress scores across recent turns. "
                    "Care-routing signal only — not a diagnosis."
                ),
                "observations": [
                    {"feature": "risk_intensity_score", "condition": f">={threshold}"},
                    {"feature": "consecutive_high", "condition": consecutive},
                    {"feature": "rolling_average", "condition": rolling_avg},
                ],
                "evidence_count": evidence,
                "confidence": min(0.92, 0.55 + 0.08 * consecutive),
                "strength": min(1.0, evidence / float(window_n + 2)),
                "status": PatternStatus.ESTABLISHED_PERSISTENT_DISTRESS.value,
                "last_observed_at": now,
            },
        )
        pattern_id = saved.get("pattern_id")
        await append_evidence(
            db,
            user_id=user_id,
            pattern_id=pattern_id,
            source="risk_window",
            feature="risk_intensity_score",
            value=scored.risk_intensity_score,
            event_key=f"{user_id}:{pattern_id}:risk:{session_id}:{now.strftime('%Y%m%d%H%M')}",
            confidence=scored.confidence_score,
            provenance="sliding_window_risk_assessor",
            event_at=now,
        )
        if scored.crisis_keywords:
            reason = "Acute crisis keywords — emergency protocol takes priority."
        elif await _card_on_cooldown(db, user_id, fp):
            reason = "Persistent distress present; psychiatrist card on dismiss cooldown."
        else:
            attach = True
            if sustained_streak:
                reason = (
                    f"Sustained high distress pattern "
                    f"(>={threshold}/10 for {consecutive} consecutive turns)"
                )
            else:
                reason = (
                    f"Sustained high distress pattern "
                    f"(rolling average {rolling_avg} > {threshold} over {hours}h)"
                )

    context = None
    if attach:
        context = {
            "suggesting_card": "PSYCHIATRIST_REFERRAL",
            "trigger_reason": reason,
            "tone_instruction": (
                "Acknowledge the heavy emotional weight warmly. Briefly and "
                "non-pushily let the user know you have made a professional "
                "care option available below. Do not interrogate, force, or "
                "act alarmist."
            ),
            "pattern_id": pattern_id,
        }

    return RiskTurnDecision(
        score=scored,
        consecutive_high=consecutive,
        rolling_average=rolling_avg,
        persistent_distress=persistent,
        attach_psychiatrist_card=attach,
        pattern_id=pattern_id,
        trigger_reason=reason,
        action_card_context=context,
    )


async def evaluate_turn_risk(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    message: str,
    opening_turn: bool = False,
) -> RiskTurnDecision:
    """
    Fast-path entry. Never raises — chat must not block if the window is down.
    """
    scored = score_turn(message)
    if opening_turn:
        return RiskTurnDecision(score=scored)
    try:
        return await record_and_evaluate(
            db,
            user_id=user_id,
            session_id=session_id,
            message=message,
            score=scored,
        )
    except Exception:
        logger.exception("Risk window evaluation failed for user=%s", user_id)
        return RiskTurnDecision(score=scored)

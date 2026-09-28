"""One optional next step per turn. The meditation ranker is the only ranker."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from services.engagement_guard import reject_engagement_features
from services.safety_class import SafetyClass

OUTCOMES = frozenset({"COMPLETED", "HELPFUL", "NOT_HELPFUL", "DISMISSED"})
COLLECTION = "stepping_outcomes"


async def choose_stepping_stone(
    db,
    *,
    user_id: str,
    message: str,
    opening_turn: bool,
    safety_class: SafetyClass,
) -> Dict[str, Any]:
    if opening_turn or safety_class is not SafetyClass.NONE:
        return {"kind": "none"}
    from services.meditation.service import prepare_turn_offer

    offer = await prepare_turn_offer(
        db, user_id=user_id, message=message, opening_turn=opening_turn
    )
    if offer.get("decision") == "RECOMMEND_MEDITATION":
        return {"kind": "meditation", "offer": offer}
    return {"kind": "none"}


async def ensure_stepping_indexes(db) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("execution_nonce", 1)],
        unique=True,
    )


async def record_step_outcome(
    db,
    *,
    user_id: str,
    execution_nonce: str,
    outcome: str,
    kind: str = "meditation",
) -> Dict[str, Any]:
    """First explicit outcome wins. A later label cannot rewrite it."""
    label = (outcome or "").upper()
    if label not in OUTCOMES:
        raise ValueError("unsupported stepping-stone outcome")
    nonce = (execution_nonce or "").strip()
    if not nonce:
        raise ValueError("execution_nonce is required")
    reject_engagement_features({"kind": kind, "outcome": label})
    existing = await db[COLLECTION].find_one(
        {"user_id": user_id, "execution_nonce": nonce}
    )
    if existing:
        return {
            "execution_nonce": nonce,
            "outcome": existing.get("outcome"),
            "kind": existing.get("kind"),
            "idempotent": True,
        }
    doc = {
        "user_id": user_id,
        "execution_nonce": nonce,
        "outcome": label,
        "kind": kind,
        "recorded_at": datetime.now(timezone.utc),
    }
    await db[COLLECTION].insert_one(doc)
    return {"execution_nonce": nonce, "outcome": label, "kind": kind, "idempotent": False}

"""Safety, consent, and crisis-flow eligibility. No new classifiers."""

from __future__ import annotations

from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import get_settings
from services.apm import contains_crisis_signal, personalization_enabled
from services.inner_council import CouncilStance
from services.safety_class import SafetyClass, classify_message


async def personalization_allowed(db: AsyncIOMotorDatabase, user_id: str) -> bool:
    return await personalization_enabled(db, user_id)


def safety_suppression_reason(
    message: str,
    *,
    risk_intensity: float = 1.0,
    persistent_distress: bool = False,
    council: Optional[CouncilStance] = None,
) -> str:
    """Map the existing safety/risk contract onto proactive suppression."""
    settings = get_settings()
    label = classify_message(message or "")
    if label is not SafetyClass.NONE:
        return f"safety_{label.value.lower()}"
    if contains_crisis_signal(message or ""):
        return "safety_crisis_keyword"
    if council is not None and council.risk_band == "crisis_adjacent":
        return "safety_crisis_adjacent"
    if persistent_distress:
        return "safety_persistent_distress"
    if float(risk_intensity) >= float(settings.RISK_HIGH_THRESHOLD):
        return "safety_high_risk"
    return ""


async def escalation_blocks_proactive(
    db: AsyncIOMotorDatabase, user_id: str
) -> bool:
    if not user_id:
        return False
    try:
        case = await db["escalation_cases"].find_one(
            {
                "user_id": user_id,
                "status": {"$in": ["fast_track_open", "open"]},
            },
            {"_id": 1},
        )
    except Exception:
        return False
    return bool(case) and isinstance(case, dict)


async def eligibility_reason(
    db: AsyncIOMotorDatabase,
    user_id: str,
    message: str,
    *,
    risk_intensity: float = 1.0,
    persistent_distress: bool = False,
    council: Optional[CouncilStance] = None,
) -> str:
    if not (user_id or "").strip():
        return "missing_user"
    safety = safety_suppression_reason(
        message,
        risk_intensity=risk_intensity,
        persistent_distress=persistent_distress,
        council=council,
    )
    if safety:
        return safety
    if await escalation_blocks_proactive(db, user_id):
        return "safety_escalation_open"
    return ""

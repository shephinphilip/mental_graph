"""Purpose-specific consent. Personalization still mirrors the existing boolean."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import HTTPException

PURPOSES = (
    "personalization",
    "clinical_share",
    "validated_assessment",
    "institution_visibility",
    "analytics_research",
    "government_aggregation",
)
COLLECTION = "consent_grants"


async def set_grant(
    db,
    user_id: str,
    purpose: str,
    *,
    enabled: bool,
    source: str = "student",
) -> Dict[str, Any]:
    if source == "parent":
        raise HTTPException(status_code=403, detail="ACTOR_POLICY_PENDING")
    if purpose not in PURPOSES:
        raise HTTPException(status_code=422, detail="unknown_purpose")
    doc = {
        "user_id": user_id,
        "purpose": purpose,
        "enabled": bool(enabled),
        "source": source,
        "updated_at": datetime.now(timezone.utc),
    }
    await db[COLLECTION].update_one(
        {"user_id": user_id, "purpose": purpose},
        {"$set": doc},
        upsert=True,
    )
    if purpose == "personalization":
        from backend_core.users import set_personalization_consent

        saved = await set_personalization_consent(db, user_id, enabled)
        if not saved:
            raise HTTPException(status_code=404, detail="Active user not found")
    return doc


async def grant_enabled(db, user_id: str, purpose: str) -> bool:
    if purpose != "personalization":
        row = await db[COLLECTION].find_one({"user_id": user_id, "purpose": purpose})
        return bool(row and row.get("enabled"))
    row = await db[COLLECTION].find_one({"user_id": user_id, "purpose": purpose})
    if row is not None:
        return bool(row.get("enabled"))
    from services.apm import personalization_enabled

    return await personalization_enabled(db, user_id)


def institution_may_read_transcripts() -> bool:
    return False


def government_aggregation_active() -> bool:
    return False


INSTRUMENT_CATALOG: tuple = ()


def start_validated_assessment() -> None:
    if not INSTRUMENT_CATALOG:
        raise HTTPException(status_code=409, detail="instrument_catalog_empty")


async def ensure_consent_indexes(db) -> None:
    await db[COLLECTION].create_index([("user_id", 1), ("purpose", 1)], unique=True)
    await db[COLLECTION].create_index([("user_id", 1), ("purpose", 1)], unique=True)

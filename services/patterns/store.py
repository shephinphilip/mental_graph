"""Pattern store: user_patterns + pattern_evidence with user isolation."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

logger = logging.getLogger(__name__)

PATTERNS_COLLECTION = "user_patterns"
EVIDENCE_COLLECTION = "pattern_evidence"


async def ensure_pattern_indexes(db: AsyncIOMotorDatabase) -> None:
    patterns = db[PATTERNS_COLLECTION]
    evidence = db[EVIDENCE_COLLECTION]

    await patterns.create_index(
        [("user_id", 1), ("fingerprint", 1)],
        unique=True,
        name="user_pattern_fingerprint_unique",
    )
    await patterns.create_index(
        [
            ("user_id", 1),
            ("status", 1),
            ("confidence", -1),
            ("last_observed_at", -1),
        ],
        name="user_status_confidence_recency",
    )
    await patterns.create_index(
        [("user_id", 1), ("pattern_type", 1), ("last_observed_at", -1)],
        name="user_type_recency",
    )
    await patterns.create_index(
        [("user_id", 1), ("domains", 1)],
        name="user_domains",
    )
    await patterns.create_index(
        [("user_id", 1), ("pattern_id", 1)],
        unique=True,
        name="user_pattern_id_unique",
    )

    await evidence.create_index(
        [("user_id", 1), ("pattern_id", 1), ("created_at", -1)],
        name="user_pattern_evidence_timeline",
    )
    await evidence.create_index(
        [("user_id", 1), ("event_key", 1)],
        unique=True,
        name="user_evidence_event_key_unique",
    )


def new_pattern_id() -> str:
    return f"pat_{uuid.uuid4().hex[:16]}"


async def get_pattern_by_fingerprint(
    db: AsyncIOMotorDatabase, user_id: str, fingerprint: str
) -> Optional[Dict[str, Any]]:
    return await db[PATTERNS_COLLECTION].find_one(
        {"user_id": user_id, "fingerprint": fingerprint}
    )


async def get_pattern_by_id(
    db: AsyncIOMotorDatabase, user_id: str, pattern_id: str
) -> Optional[Dict[str, Any]]:
    return await db[PATTERNS_COLLECTION].find_one(
        {"user_id": user_id, "pattern_id": pattern_id}
    )


async def list_active_patterns(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    min_confidence: float,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    cursor = (
        db[PATTERNS_COLLECTION]
        .find(
            {
                "user_id": user_id,
                "status": {"$in": ["EMERGING", "ESTABLISHED"]},
                "confidence": {"$gte": min_confidence},
            }
        )
        .sort([("confidence", -1), ("last_observed_at", -1)])
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def upsert_pattern(
    db: AsyncIOMotorDatabase,
    user_id: str,
    doc: Dict[str, Any],
) -> Dict[str, Any]:
    """Insert or merge a pattern document scoped to user_id."""
    now = datetime.now(timezone.utc)
    fingerprint = doc["fingerprint"]
    existing = await get_pattern_by_fingerprint(db, user_id, fingerprint)
    if existing:
        update = {
            "evidence_count": doc.get("evidence_count", existing.get("evidence_count", 1)),
            "confidence": doc.get("confidence", existing.get("confidence", 0.0)),
            "strength": doc.get("strength", existing.get("strength", 0.0)),
            "status": doc.get("status", existing.get("status")),
            "last_observed_at": doc.get("last_observed_at", now),
            "description": doc.get("description") or existing.get("description"),
            "observations": doc.get("observations") or existing.get("observations"),
            "contradiction_count": doc.get(
                "contradiction_count", existing.get("contradiction_count", 0)
            ),
            "confirm_count": doc.get("confirm_count", existing.get("confirm_count", 0)),
            "disagree_count": doc.get("disagree_count", existing.get("disagree_count", 0)),
            "updated_at": now,
        }
        await db[PATTERNS_COLLECTION].update_one(
            {"user_id": user_id, "pattern_id": existing["pattern_id"]},
            {"$set": update},
        )
        existing.update(update)
        return existing

    pattern_id = doc.get("pattern_id") or new_pattern_id()
    record = {
        **doc,
        "user_id": user_id,
        "pattern_id": pattern_id,
        "created_at": now,
        "updated_at": now,
        "first_observed_at": doc.get("first_observed_at", now),
        "last_observed_at": doc.get("last_observed_at", now),
        "confirm_count": doc.get("confirm_count", 0),
        "disagree_count": doc.get("disagree_count", 0),
        "contradiction_count": doc.get("contradiction_count", 0),
        "decay_rate": doc.get("decay_rate"),
    }
    await db[PATTERNS_COLLECTION].insert_one(record)
    return record


async def append_evidence(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    pattern_id: str,
    source: str,
    feature: str,
    value: Any,
    event_key: str,
    confidence: float,
    provenance: str,
    event_at: Optional[datetime] = None,
) -> bool:
    """Append-only evidence. Returns False if duplicate event_key."""
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user_id,
        "pattern_id": pattern_id,
        "source": source,
        "feature": feature,
        "value": value,
        "event_key": event_key,
        "confidence": confidence,
        "provenance": provenance,
        "event_at": event_at or now,
        "created_at": now,
    }
    try:
        await db[EVIDENCE_COLLECTION].insert_one(doc)
        return True
    except Exception as exc:
        # DuplicateKeyError or mock collisions
        if "duplicate" in str(exc).lower() or exc.__class__.__name__ == "DuplicateKeyError":
            return False
        from pymongo.errors import DuplicateKeyError

        if isinstance(exc, DuplicateKeyError):
            return False
        raise


async def delete_user_patterns(db: AsyncIOMotorDatabase, user_id: str) -> Dict[str, int]:
    """Hard-delete patterns for consent revocation / account wipe."""
    p = await db[PATTERNS_COLLECTION].delete_many({"user_id": user_id})
    e = await db[EVIDENCE_COLLECTION].delete_many({"user_id": user_id})
    return {"patterns": p.deleted_count, "evidence": e.deleted_count}

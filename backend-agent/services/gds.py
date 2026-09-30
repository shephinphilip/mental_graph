"""Global Distress Score. Unmapped until an approved mapping exists."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config.config import logger
from services.engagement_guard import reject_engagement_features

MAPPINGS = "gds_mapping_versions"
SNAPSHOTS = "gds_snapshots"


def resolve_care_band(mapping: Optional[Dict[str, Any]]) -> str:
    if not mapping or mapping.get("status") != "APPROVED":
        return "UNMAPPED"
    if not mapping.get("approved_by") or not mapping.get("approval_reference"):
        return "UNMAPPED"
    edges = mapping.get("mapping") or {}
    if not edges:
        return "UNMAPPED"
    return str(edges.get("band") or "UNMAPPED")


async def current_mapping(db) -> Optional[Dict[str, Any]]:
    try:
        return await db[MAPPINGS].find_one({"status": "APPROVED"})
    except Exception:
        return None


async def register_mapping(db, document: Dict[str, Any]) -> Dict[str, Any]:
    """Two APPROVED rows are a failed release, not a fallback."""
    reject_engagement_features(document)
    status = document.get("status")
    if status == "APPROVED":
        if not document.get("approved_by") or not document.get("approval_reference"):
            raise ValueError("APPROVED mapping requires approved_by and approval_reference")
        existing = await db[MAPPINGS].find_one({"status": "APPROVED"})
        if existing and existing.get("version") != document.get("version"):
            raise ValueError("a second APPROVED GDS mapping is not allowed")
    document = {**document, "created_at": document.get("created_at") or datetime.now(timezone.utc)}
    await db[MAPPINGS].update_one(
        {"version": document.get("version")},
        {"$set": document},
        upsert=True,
    )
    return document


async def record_shadow(db, user_id: str, *, risk_intensity: float) -> Dict[str, Any]:
    """Store the heuristic beside an empty GDS. Never a student-facing number."""
    mapping = await current_mapping(db)
    band = resolve_care_band(mapping)
    doc = {
        "user_id": user_id,
        "shadow_intensity": round(float(risk_intensity), 2),
        "gds_value": None,
        "care_band": band,
        "recorded_at": datetime.now(timezone.utc),
    }
    reject_engagement_features(doc)
    try:
        await db[SNAPSHOTS].update_one({"user_id": user_id}, {"$set": doc}, upsert=True)
    except Exception:
        logger.exception("GDS shadow write failed user=%s", user_id)
    return doc


async def ensure_gds_indexes(db) -> None:
    await db[MAPPINGS].create_index("version", unique=True)
    await db[SNAPSHOTS].create_index("user_id", unique=True)

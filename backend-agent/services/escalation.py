"""The only writer of care cases and the only notifier."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config.config import logger
from services.gds import resolve_care_band

CASES = "escalation_cases"
EVENTS = "escalation_events"


async def _append(db, case_id: str, event: str, extra: Optional[Dict[str, Any]] = None) -> None:
    await db[EVENTS].insert_one(
        {
            "event_id": uuid.uuid4().hex[:16],
            "case_id": case_id,
            "event": event,
            "at": datetime.now(timezone.utc),
            **(extra or {}),
        }
    )


async def open_crisis_fast_track(db, user_id: str, *, session_id: str = "") -> Dict[str, Any]:
    """Keyword crisis. Does not read GDS and does not wait for a provider."""
    try:
        open_case = await db[CASES].find_one(
            {"user_id": user_id, "status": {"$in": ["fast_track_open", "open"]}}
        )
        if open_case:
            return {**open_case, "delivery": "not_configured", "duplicate": True}
        case_id = f"case_{uuid.uuid4().hex[:12]}"
        doc = {
            "case_id": case_id,
            "user_id": user_id,
            "session_id": session_id,
            "status": "fast_track_open",
            "reason": "CRISIS_KEYWORD",
            "opened_at": datetime.now(timezone.utc),
        }
        await db[CASES].insert_one(doc)
        await _append(db, case_id, "fast_track_open")
        return {**doc, "delivery": "not_configured", "duplicate": False}
    except Exception:
        logger.exception("Crisis case write failed user=%s", user_id)
        return {"delivery": "not_configured", "duplicate": False, "status": "fast_track_open"}


async def on_turn(
    db,
    user_id: str,
    *,
    risk_intensity: float,
    safety_class: str,
    trajectory: str = "unknown",
) -> Dict[str, Any]:
    """Normal turns. While the care band is unmapped, record and do not page."""
    band = "UNMAPPED"
    try:
        mapping = await db["gds_mapping_versions"].find_one({"status": "APPROVED"})
        band = resolve_care_band(mapping)
    except Exception:
        band = "UNMAPPED"
    result = {
        "care_band": band,
        "trajectory": trajectory,
        "safety_class": safety_class,
        "risk_intensity": risk_intensity,
        "delivery": "not_configured",
        "notified": False,
        "care_request": "",
    }
    if band == "UNMAPPED":
        result["reason"] = "band_unmapped"
        try:
            await _append(db, f"shadow_{user_id}", "band_unmapped", {"user_id": user_id})
        except Exception:
            logger.info("band_unmapped user=%s", user_id)
        return result
    return result


async def notify(db, case_id: str, *, provider: str = "") -> Dict[str, Any]:
    """Never report success when nothing was sent."""
    if not provider:
        return {"case_id": case_id, "delivery": "not_configured", "notified": False}
    return {"case_id": case_id, "delivery": "not_configured", "notified": False}


async def ensure_escalation_indexes(db) -> None:
    await db[CASES].create_index([("user_id", 1), ("status", 1)])
    await db[EVENTS].create_index([("case_id", 1), ("at", 1)])

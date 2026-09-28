"""Persistent dashboard preferences for the signed-in staff member."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard.constants import SETTINGS

DEFAULTS = {
    "at_risk_alerts": True,
    "weekly_digest": True,
    "report_generation_alerts": True,
    "ai_insights": True,
}

DELIVERY = {
    "weekly_digest": "stored_only",
    "at_risk_alerts": "stored_only",
    "report_generation_alerts": "inbox",
    "ai_insights": "assistant",
    "reason": (
        "Weekly digest and automatic at-risk mail are not sent: this deployment has no "
        "school mailer and does not bridge chat risk turns into the dashboard inbox. "
        "report_generation_alerts controls the inbox item created when a dashboard report is ready. "
        "ai_insights controls POST /dashboard/assistant."
    ),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_settings(db, actor) -> dict[str, Any]:
    doc = await db[SETTINGS].find_one({"school_key": actor.school_key, "user_id": actor.user_id})
    stored = bool(doc)
    values = dict(DEFAULTS)
    if doc:
        for key in DEFAULTS:
            if key in doc:
                values[key] = bool(doc[key])
    return {**values, "persisted": stored, "delivery": DELIVERY}


async def update_settings(db, actor, patch: dict[str, bool]) -> dict[str, Any]:
    current = await get_settings(db, actor)
    values = {key: current[key] for key in DEFAULTS}
    values.update(patch)
    now = _now()
    await db[SETTINGS].update_one(
        {"school_key": actor.school_key, "user_id": actor.user_id},
        {
            "$set": {**values, "updated_at": now},
            "$setOnInsert": {"created_at": now, "school_key": actor.school_key, "user_id": actor.user_id},
        },
        upsert=True,
    )
    saved = await get_settings(db, actor)
    saved["persisted"] = True
    return saved

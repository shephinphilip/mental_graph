"""Audit rows for sensitive dashboard writes. Bodies are not logged."""

from __future__ import annotations

from datetime import datetime, timezone

from config.config import logger
from dashboard.constants import AUDIT
from dashboard.identity import new_id


async def audit(db, *, actor, action: str, resource_type: str, resource_id: str) -> None:
    await db[AUDIT].insert_one(
        {
            "audit_id": new_id("aud"),
            "school_key": actor.school_key,
            "actor_user_id": actor.user_id,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "created_at": datetime.now(timezone.utc),
        }
    )
    logger.info(
        "dashboard action=%s school_key=%s actor=%s resource=%s",
        action,
        actor.school_key,
        actor.user_id,
        resource_id,
    )

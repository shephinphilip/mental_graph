"""User-chosen IANA timezone. Never inferred from IP or the server clock."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend_core.users import get_by_identifier, public_user_view


def parse_timezone(value: str) -> str:
    """Accept an IANA name such as Asia/Kolkata. Abbreviations and numeric offsets are rejected."""
    text = (value or "").strip()
    if not text or ("/" not in text and text != "UTC"):
        raise ValueError("Timezone must be an IANA name such as Asia/Kolkata")
    try:
        ZoneInfo(text)
    except (ZoneInfoNotFoundError, KeyError, ValueError) as exc:
        raise ValueError("Timezone must be an IANA name such as Asia/Kolkata") from exc
    return text


async def set_timezone(db, identifier: str, zone: str):
    parsed = parse_timezone(zone)
    doc = await get_by_identifier(db, identifier, include_password=True)
    if not doc:
        return None
    query = {"_id": doc["_id"]} if doc.get("_id") is not None else {"user_id": doc.get("user_id")}
    await db["users"].update_one(
        query,
        {
            "$set": {
                "timezone": parsed,
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    updated = await get_by_identifier(db, identifier)
    return public_user_view(updated) if updated else None

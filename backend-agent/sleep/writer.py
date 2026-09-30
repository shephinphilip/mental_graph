"""Create a sleep_logs row for the authenticated user only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sleep.identity import identity_keys, owns_claimed_id
from sleep.reader import duration_from_times, sleep_cycle_date, stored_duration


async def create_sleep_log(
    db,
    authenticated_user_id: str,
    *,
    bedtime: str,
    wake_up_time: str,
    date: str,
    total_duration_minutes: Optional[int] = None,
    claimed_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    keys = await identity_keys(db, authenticated_user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot write sleep for another user")
    if not bedtime or not wake_up_time or not date:
        raise ValueError("bedtime, wake_up_time, and date are required")
    if total_duration_minutes is not None:
        minutes = stored_duration(
            {
                "bedtime": bedtime,
                "wake_up_time": wake_up_time,
                "total_duration_minutes": total_duration_minutes,
            }
        )
    else:
        minutes = duration_from_times(bedtime, wake_up_time)
    if minutes is None:
        raise ValueError("Could not read a duration from the times provided")
    try:
        cycle_date = sleep_cycle_date(date, bedtime)
    except ValueError as exc:
        raise ValueError("date must be YYYY-MM-DD") from exc
    doc = {
        "user_id": authenticated_user_id,
        "bedtime": bedtime,
        "wake_up_time": wake_up_time,
        "total_duration_minutes": minutes,
        "date": cycle_date,
        "created_at": datetime.now(timezone.utc),
    }
    await db["sleep_logs"].insert_one(doc)
    return doc

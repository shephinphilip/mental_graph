"""Indexes for mood_logs and habit_events. Safe to run again."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

MOODS = "mood_logs"
HABITS = "habit_events"


async def ensure_tracking_indexes(db) -> None:
    await db[MOODS].create_index(
        [("user_id", 1), ("logged_at", -1)], name="mood_logs_user_logged"
    )
    await db[MOODS].create_index(
        [("user_id", 1), ("created_at", -1)], name="mood_logs_user_created"
    )
    # Offline clients retry. The same check-in must not land twice.
    await db[MOODS].create_index(
        [("user_id", 1), ("client_event_id", 1)],
        unique=True,
        partialFilterExpression={"client_event_id": {"$type": "string"}},
        name="mood_logs_client_event_unique",
    )
    await db[HABITS].create_index(
        [("user_id", 1), ("habit_id", 1)], unique=True, name="habit_events_user_habit"
    )
    await db[HABITS].create_index(
        [("user_id", 1), ("status", 1)], name="habit_events_user_status"
    )
    logger.info("Tracking indexes ensured")

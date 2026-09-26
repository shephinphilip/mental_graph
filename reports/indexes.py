"""Indexes for session_reports. Safe to run more than once."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

COLLECTION = "session_reports"


async def ensure_report_indexes(db) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("session_id", 1)],
        unique=True,
        name="uniq_session_report",
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("created_at", -1)],
        name="session_reports_user_created",
    )
    logger.info("Session report indexes ensured")

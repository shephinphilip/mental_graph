"""Indexes for the three consultation collections. Safe to run again."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EVALUATIONS = "psychiatric_evaluations"
NOTIFICATIONS = "consultation_notifications"
AUDIT = "psychiatric_evaluation_audit"


async def ensure_consultation_indexes(db) -> None:
    await db[EVALUATIONS].create_index(
        [("userId", 1), ("evaluation_timestamp", -1)],
        name="evaluations_user_recency",
    )
    await db[EVALUATIONS].create_index(
        [("userId", 1), ("status", 1), ("evaluation_timestamp", -1)],
        name="evaluations_user_status",
    )
    await db[NOTIFICATIONS].create_index(
        [("userId", 1), ("read", 1), ("created_at", -1)],
        name="notifications_user_unread",
    )
    await db[AUDIT].create_index(
        [("userId", 1), ("timestamp", -1)],
        name="audit_user_recency",
    )
    logger.info("Consultation indexes ensured")

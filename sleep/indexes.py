"""Indexes for the sleep queries the readers actually run."""

from __future__ import annotations


async def ensure_sleep_indexes(db) -> None:
    await db["sleep_logs"].create_index(
        [("user_id", 1), ("created_at", -1)],
        name="sleep_user_created",
    )
    await db["sleep_logs"].create_index(
        [("user_id", 1), ("date", -1)],
        name="sleep_user_date",
    )

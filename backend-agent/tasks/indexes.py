"""One document per user per day, readable by date."""

from __future__ import annotations


async def ensure_task_indexes(db) -> None:
    await db["daily_tasks"].create_index(
        [("user_id", 1), ("date", -1)],
        unique=True,
        name="daily_tasks_user_date",
    )

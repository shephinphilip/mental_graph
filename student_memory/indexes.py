"""Indexes for student_memories. Safe to run again."""

from __future__ import annotations

from config.config import logger

COLLECTION = "student_memories"


async def ensure_student_memory_indexes(db) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("key", 1)], unique=True, name="student_memory_user_key"
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("importance", -1), ("last_confirmed_at", -1)],
        name="student_memory_user_importance",
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("category", 1)], name="student_memory_user_category"
    )
    logger.info("Student memory indexes ensured")

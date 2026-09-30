"""Indexes the journal queries already need. No extras."""

from __future__ import annotations


async def ensure_journal_indexes(db) -> None:
    logs = db["journal_entries"]
    await logs.create_index("user_id", name="journal_user")
    await logs.create_index("timestamp", name="journal_timestamp")
    await logs.create_index(
        [("user_id", 1), ("timestamp", -1)],
        name="journal_user_timestamp",
    )
    await logs.create_index(
        [("user_id", 1), ("is_favorite", 1)],
        name="journal_user_favorite",
    )
    await logs.create_index("tags", name="journal_tags")

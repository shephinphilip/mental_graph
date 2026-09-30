"""Optional voice-session metadata. Conversation text stays in ``messages``."""

from __future__ import annotations

from config.config import logger


async def ensure_voice_indexes(db) -> None:
    await db["voice_sessions"].create_index(
        [("user_id", 1), ("voice_session_id", 1)],
        unique=True,
        name="user_voice_session_unique",
    )
    await db["voice_sessions"].create_index(
        [("user_id", 1), ("chat_session_id", 1), ("created_at", -1)],
        name="user_chat_voice_created",
    )
    logger.info("Voice session indexes ensured")

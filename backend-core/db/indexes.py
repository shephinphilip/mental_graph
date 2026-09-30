"""Startup index orchestration. Domain modules still declare their own indexes.

User indexes are ensured here. Each application registers the indexes it owns
through ``register_index_hook`` before the process lifespan runs. This module
does not import mental-health or dashboard packages.
"""

from __future__ import annotations

from config.config import logger

_HOOKS: list = []


def register_index_hook(fn) -> None:
    if fn not in _HOOKS:
        _HOOKS.append(fn)


async def ensure_all_indexes(db) -> None:
    await db["users"].create_index("email", unique=True)
    await db["users"].create_index("user_id", unique=True)
    for hook in list(_HOOKS):
        await hook(db)
    logger.info("All domain indexes ensured")

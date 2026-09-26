"""
scripts/consolidate_memory.py — Weekly or monthly profile summarisation.

Run from cron or a scheduler:

    python scripts/consolidate_memory.py            # every user with facts
    python scripts/consolidate_memory.py usr_abc    # one user

Reads student_memories, archives faded facts, and rewrites
users.memory_summary and users.key_takeaways from the strongest ones.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

from config import get_settings
from student_memory.indexes import COLLECTION
from student_memory.store import consolidate_student_memory


async def main(user_ids: list[str]) -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.MONGODB_URI, serverSelectionTimeoutMS=8000)
    db = client[settings.DATABASE_NAME]
    try:
        if not user_ids:
            user_ids = await db[COLLECTION].distinct("user_id")
        for user_id in user_ids:
            result = await consolidate_student_memory(db, user_id)
            print(f"{user_id}: kept={result['kept']} archived={result['archived']}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))

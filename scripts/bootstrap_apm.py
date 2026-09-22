"""Bootstrap background-only APM facts from the existing Mongo graph."""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from motor.motor_asyncio import AsyncIOMotorClient

from config import get_settings
from services.apm import bootstrap_existing_graph, ensure_apm_indexes


async def main() -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.DATABASE_NAME]
    await ensure_apm_indexes(db)
    user_ids = await db["graph_nodes"].distinct("user_id")
    total = 0
    for user_id in user_ids:
        count = await bootstrap_existing_graph(db, user_id)
        total += count
        print(f"{user_id}: {count} APM facts bootstrapped")
    print(f"Total: {total}")
    client.close()


if __name__ == "__main__":
    asyncio.run(main())

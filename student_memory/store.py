"""student_memories: one row per user per fact key."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.config import get_settings, logger
from student_memory.indexes import COLLECTION
from student_memory.models import CATEGORIES, normalize_facts

CONFIRM_BOOST = 0.08


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def effective_importance(doc: Dict[str, Any], *, now: Optional[datetime] = None) -> float:
    """Stored importance, reduced a little for each day since it was last confirmed."""
    now = now or datetime.now(timezone.utc)
    base = float(doc.get("importance") or 0.0)
    last = _as_dt(doc.get("last_confirmed_at")) or _as_dt(doc.get("created_at"))
    if not last:
        return base
    days = max(0.0, (now - last).total_seconds() / 86400.0)
    return max(0.0, round(base - get_settings().MEMORY_DECAY_PER_DAY * days, 4))


async def upsert_facts(
    db,
    user_id: str,
    session_id: str,
    raw_facts: Any,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Insert new facts. A repeated fact is confirmed, not duplicated."""
    facts = normalize_facts(raw_facts)
    if not facts:
        return {"inserted": 0, "confirmed": 0}
    now = now or datetime.now(timezone.utc)
    inserted = confirmed = 0
    for fact in facts:
        existing = await db[COLLECTION].find_one({"user_id": user_id, "key": fact["key"]})
        if existing:
            sessions = list(existing.get("source_sessions") or [])
            if session_id not in sessions:
                sessions.append(session_id)
            importance = min(1.0, max(float(existing.get("importance") or 0), fact["importance"]) + CONFIRM_BOOST)
            await db[COLLECTION].update_one(
                {"user_id": user_id, "key": fact["key"]},
                {
                    "$set": {
                        "importance": importance,
                        "last_confirmed_at": now,
                        "source_sessions": sessions[-20:],
                        "archived": False,
                    }
                },
            )
            confirmed += 1
            continue
        await db[COLLECTION].insert_one(
            {
                "memory_id": f"mem_{uuid.uuid4().hex[:12]}",
                "user_id": user_id,
                "fact": fact["fact"],
                "key": fact["key"],
                "category": fact["category"],
                "importance": fact["importance"],
                "source_sessions": [session_id],
                "archived": False,
                "created_at": now,
                "last_confirmed_at": now,
            }
        )
        inserted += 1
    return {"inserted": inserted, "confirmed": confirmed}


async def retrieve_facts(
    db, user_id: str, *, limit: Optional[int] = None, now: Optional[datetime] = None
) -> List[Dict[str, Any]]:
    """Top facts by decayed importance. Faded facts are left out."""
    settings = get_settings()
    limit = limit or settings.MEMORY_CONTEXT_LIMIT
    now = now or datetime.now(timezone.utc)
    cursor = db[COLLECTION].find({"user_id": user_id, "archived": {"$ne": True}}).sort("importance", -1).limit(60)
    rows = await cursor.to_list(length=60)
    scored = []
    for row in rows:
        score = effective_importance(row, now=now)
        if score >= settings.MEMORY_MIN_IMPORTANCE:
            scored.append({**row, "effective_importance": score})
    scored.sort(key=lambda r: r["effective_importance"], reverse=True)
    return scored[:limit]


async def consolidate_student_memory(
    db, user_id: str, *, now: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Weekly or monthly job. Writes users.memory_summary and key_takeaways from
    the strongest facts and archives facts that have faded.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    cursor = db[COLLECTION].find({"user_id": user_id}).sort("importance", -1).limit(200)
    rows = await cursor.to_list(length=200)
    kept: List[Dict[str, Any]] = []
    archived = 0
    for row in rows:
        score = effective_importance(row, now=now)
        if score < settings.MEMORY_MIN_IMPORTANCE:
            if not row.get("archived"):
                await db[COLLECTION].update_one(
                    {"user_id": user_id, "key": row.get("key")},
                    {"$set": {"archived": True, "archived_at": now}},
                )
                archived += 1
            continue
        kept.append({**row, "effective_importance": score})
    kept.sort(key=lambda r: r["effective_importance"], reverse=True)

    by_category: Dict[str, List[str]] = {}
    for row in kept:
        by_category.setdefault(row.get("category") or "OTHER", []).append(str(row.get("fact")))
    parts = []
    for category in CATEGORIES:
        facts = by_category.get(category)
        if facts:
            parts.append(f"{category.title()}: " + "; ".join(facts[:3]) + ".")
    summary = " ".join(parts)[:1500]
    takeaways = [str(row.get("fact")) for row in kept[:5]]
    if summary or takeaways:
        await db["users"].update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "memory_summary": summary,
                    "key_takeaways": takeaways,
                    "memory_consolidated_at": now,
                }
            },
        )
    return {"kept": len(kept), "archived": archived, "summary_chars": len(summary)}


async def delete_student_memory(db, user_id: str) -> int:
    result = await db[COLLECTION].delete_many({"user_id": user_id})
    return int(getattr(result, "deleted_count", 0) or 0)

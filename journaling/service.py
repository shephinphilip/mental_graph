"""
Sole owner of journal_entries.

Chat, reports, and the pattern engine call these functions.
They do not query the collection themselves.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId

from config import get_settings
from journaling.identity import identity_keys, identity_query, owns_claimed_id
from journaling.models import clean_tags, topics_from, validate_entry
from services.apm import contains_crisis_signal


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _same(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    return (
        str(left.get("title") or "").strip() == str(right.get("title") or "").strip()
        and str(left.get("content") or "").strip() == str(right.get("content") or "").strip()
        and left.get("mood") == right.get("mood")
    )


async def _fetch(db, keys: List[str], *, extra: Optional[Dict[str, Any]] = None, limit: int = 50):
    query = identity_query(keys)
    if extra:
        query.update(extra)
    cursor = db["journal_entries"].find(query)
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("timestamp", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=limit)
    rows = [doc for doc in docs if isinstance(doc, dict)]
    rows.sort(key=lambda doc: _as_dt(doc.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows


def public_entry(doc: Dict[str, Any], *, preview_chars: Optional[int] = None) -> Dict[str, Any]:
    """Student-facing shape. Full text only when preview_chars is omitted."""
    content = str(doc.get("content") or "")
    if preview_chars is not None:
        content = content[:preview_chars]
    return {
        "entry_id": str(doc.get("_id")),
        "user_id": doc.get("user_id"),
        "mood": doc.get("mood"),
        "title": doc.get("title"),
        "content": content,
        "tags": doc.get("tags") or [],
        "time_spent": doc.get("time_spent") or 0,
        "is_favorite": bool(doc.get("is_favorite")),
        "favorited_at": doc.get("favorited_at"),
        "timestamp": doc.get("timestamp"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def create_journal_entry(
    db,
    authenticated_user_id: str,
    *,
    title: str,
    content: str,
    mood: str,
    tags: Optional[List[str]] = None,
    time_spent: int = 0,
    claimed_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Insert one entry for the authenticated user.

    The same title, content, and mood posted again within two minutes
    returns the original row instead of a second document.
    """
    keys = await identity_keys(db, authenticated_user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot write a journal for another user")
    title = title.strip()
    content = content.strip()
    spent = int(time_spent or 0)
    validate_entry(title=title, content=content, mood=mood, time_spent=spent)
    cleaned = clean_tags(tags)
    now = datetime.now(timezone.utc)
    recent = await _fetch(db, keys, limit=5)
    for existing in recent:
        created = _as_dt(existing.get("created_at"))
        if created and (now - created) <= timedelta(minutes=2) and _same(
            existing, {"title": title, "content": content, "mood": mood}
        ):
            existing["duplicate"] = True
            return existing
    doc = {
        "_id": ObjectId(),
        "user_id": authenticated_user_id,
        "mood": mood,
        "title": title,
        "content": content,
        "tags": cleaned,
        "time_spent": spent,
        "is_favorite": False,
        "favorited_at": None,
        "timestamp": now,
        "created_at": now,
        "updated_at": now,
    }
    await db["journal_entries"].insert_one(doc)
    doc["duplicate"] = False
    return doc


async def get_entry(db, authenticated_user_id: str, entry_id: str) -> Optional[Dict[str, Any]]:
    keys = await identity_keys(db, authenticated_user_id)
    try:
        oid = ObjectId(entry_id)
    except Exception:
        return None
    doc = await db["journal_entries"].find_one({"_id": oid})
    if not doc or str(doc.get("user_id")) not in keys:
        return None
    return doc


async def recent_entries(db, authenticated_user_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    settings = get_settings()
    keys = await identity_keys(db, authenticated_user_id)
    size = limit if limit is not None else settings.JOURNAL_CONTEXT_LIMIT
    return await _fetch(db, keys, limit=max(1, int(size)))


async def past_reflections(db, authenticated_user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    settings = get_settings()
    rows = await _fetch(db, await identity_keys(db, authenticated_user_id), limit=limit + settings.JOURNAL_CONTEXT_LIMIT)
    return rows[settings.JOURNAL_CONTEXT_LIMIT : settings.JOURNAL_CONTEXT_LIMIT + limit]


async def calendar_data(db, authenticated_user_id: str) -> Dict[str, int]:
    rows = await _fetch(db, await identity_keys(db, authenticated_user_id), limit=500)
    counts: Dict[str, int] = {}
    for row in rows:
        stamp = _as_dt(row.get("timestamp"))
        if stamp is None:
            continue
        day = stamp.date().isoformat()
        counts[day] = counts.get(day, 0) + 1
    return counts


async def favorites(db, authenticated_user_id: str) -> List[Dict[str, Any]]:
    keys = await identity_keys(db, authenticated_user_id)
    return await _fetch(db, keys, extra={"is_favorite": True}, limit=50)


async def stats(db, authenticated_user_id: str) -> Dict[str, int]:
    rows = await _fetch(db, await identity_keys(db, authenticated_user_id), limit=500)
    return {
        "entry_count": len(rows),
        "favorite_count": sum(1 for row in rows if row.get("is_favorite")),
    }


async def monthly_mindfulness(db, authenticated_user_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """This month's selected moods. Not an inferred mindfulness score."""
    now = now or datetime.now(timezone.utc)
    rows = await _fetch(db, await identity_keys(db, authenticated_user_id), limit=500)
    counts: Dict[str, int] = {}
    total = 0
    for row in rows:
        stamp = _as_dt(row.get("timestamp"))
        if stamp is None or stamp.year != now.year or stamp.month != now.month:
            continue
        mood = str(row.get("mood") or "")
        counts[mood] = counts.get(mood, 0) + 1
        total += 1
    return {"month": now.strftime("%Y-%m"), "entry_count": total, "mood_counts": counts}


async def entries_for_patterns(db, authenticated_user_id: str, *, limit: int = 30) -> List[Dict[str, Any]]:
    """
    Pattern fuel. Crisis entries are omitted.

    The original content stays on the journal row and is not copied out.
    """
    rows = await _fetch(db, await identity_keys(db, authenticated_user_id), limit=limit)
    out = []
    for row in rows:
        if contains_crisis_signal(str(row.get("content") or "")) or contains_crisis_signal(
            str(row.get("title") or "")
        ):
            continue
        stamp = _as_dt(row.get("timestamp"))
        tags = row.get("tags") or []
        out.append(
            {
                "mood": row.get("mood"),
                "title": row.get("title"),
                "date": stamp.date().isoformat() if stamp else "",
                "tags": tags,
                "topics": topics_from(str(row.get("title") or ""), str(row.get("content") or ""), tags),
                "observed_at": stamp,
                "source": "journal_entries",
            }
        )
    return out

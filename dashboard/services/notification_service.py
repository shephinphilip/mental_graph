"""School inbox. A notification is visible only inside its school and audience."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from dashboard.constants import NOTIFICATIONS
from dashboard.events.publisher import publish
from dashboard.identity import isoformat, new_id
from dashboard.repositories.dashboard_repository import _find
from dashboard.services.paging import paginate


def _now() -> datetime:
    return datetime.now(timezone.utc)


def visible(note: dict, actor) -> bool:
    if note.get("school_key") != actor.school_key:
        return False
    recipient = note.get("recipient_user_id")
    if recipient:
        return recipient == actor.user_id
    audience = set(note.get("audience") or [])
    if not audience:
        return True
    return bool(audience & set(actor.roles))


def _public(note: dict) -> dict[str, Any]:
    return {
        "notification_id": note.get("notification_id"),
        "category": note.get("category"),
        "title": note.get("title"),
        "body": note.get("body") or "",
        "read": bool(note.get("read")),
        "created_at": note.get("created_at"),
        "resource_type": note.get("resource_type"),
        "resource_id": note.get("resource_id"),
    }


async def create_notification(
    db,
    *,
    actor,
    category: str,
    title: str,
    body: str,
    recipient_user_id: Optional[str] = None,
    audience: Optional[list[str]] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
) -> dict:
    doc = {
        "notification_id": new_id("ntf"),
        "school_key": actor.school_key,
        "recipient_user_id": recipient_user_id,
        "audience": list(audience or []),
        "category": category,
        "title": title[:140],
        "body": body[:2000],
        "read": False,
        "created_at": _now(),
        "resource_type": resource_type,
        "resource_id": resource_id,
    }
    await db[NOTIFICATIONS].insert_one(doc)
    publish(
        actor.school_key,
        "notification_created",
        notification_id=doc["notification_id"],
        category=category,
    )
    return doc


async def list_notifications(db, actor, *, unread_only: bool, page: int, limit: int, cursor: Optional[str]):
    rows = await _find(
        db[NOTIFICATIONS],
        {"school_key": actor.school_key},
        {"_id": 0},
        500,
        ("created_at", -1),
    )
    visible_rows = [row for row in rows if visible(row, actor)]
    if unread_only:
        visible_rows = [row for row in visible_rows if not row.get("read")]
    chunk, meta = paginate(
        visible_rows,
        limit=limit,
        page=page,
        cursor=cursor,
        reverse=True,
        key=lambda row: f"{isoformat(row.get('created_at')) or ''}|{row.get('notification_id')}",
    )
    return [_public(row) for row in chunk], meta


async def mark_read(db, actor, notification_id: str) -> dict:
    note = await db[NOTIFICATIONS].find_one(
        {"school_key": actor.school_key, "notification_id": notification_id},
        {"_id": 0},
    )
    if not note or not visible(note, actor):
        raise HTTPException(status_code=404, detail="Notification not found")
    note["read"] = True
    note["read_at"] = _now()
    await db[NOTIFICATIONS].update_one(
        {"school_key": actor.school_key, "notification_id": notification_id},
        {"$set": {"read": True, "read_at": note["read_at"]}},
    )
    return _public(note)


async def mark_all_read(db, actor) -> dict:
    rows = await _find(
        db[NOTIFICATIONS],
        {"school_key": actor.school_key, "read": False},
        {"_id": 0},
        500,
        ("created_at", -1),
    )
    updated = 0
    now = _now()
    for note in rows:
        if not visible(note, actor):
            continue
        await db[NOTIFICATIONS].update_one(
            {"school_key": actor.school_key, "notification_id": note.get("notification_id")},
            {"$set": {"read": True, "read_at": now}},
        )
        updated += 1
    return {"updated": updated}

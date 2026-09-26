"""
One daily_tasks document per user per calendar day (UTC).

Report tasks are appended. Existing rows are not replaced.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tasks.identity import identity_keys, identity_query, owns_claimed_id
from tasks.validate import validate_proposals

logger = logging.getLogger(__name__)

COLLECTION = "daily_tasks"


def server_today(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date().isoformat()


def new_task_id(prefix: str = "task") -> str:
    return f"{prefix}_{secrets.token_hex(4)}"


def public_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """Shape for the task UI. Internal source ids stay off this object."""
    return {
        "id": task.get("id"),
        "title": task.get("title"),
        "description": task.get("description") or "",
        "completed": bool(task.get("completed")),
        "is_custom": bool(task.get("is_custom")),
    }


def visible_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [public_task(task) for task in tasks if not task.get("is_deleted")]


async def _days(db, user_id: str, *, limit: int = 3) -> List[Dict[str, Any]]:
    keys = await identity_keys(db, user_id)
    cursor = db[COLLECTION].find(identity_query(keys))
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("date", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    docs = await cursor.to_list(length=limit)
    rows = [doc for doc in docs if isinstance(doc, dict)]
    rows.sort(key=lambda doc: str(doc.get("date") or ""), reverse=True)
    return rows[:limit]


async def recent_task_days(db, user_id: str, *, days: int = 3) -> List[Dict[str, Any]]:
    """Most recent day documents, newest date first. Not three loose tasks."""
    return await _days(db, user_id, limit=max(1, int(days)))


async def today_document(db, user_id: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    date = server_today(now)
    keys = await identity_keys(db, user_id)
    return await db[COLLECTION].find_one({**identity_query(keys), "date": date})


async def ensure_today(db, user_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Create today's document only when it is missing.

    An existing day, including report tasks already stored, is left as it is.
    """
    now = now or datetime.now(timezone.utc)
    date = server_today(now)
    await db[COLLECTION].update_one(
        {"user_id": user_id, "date": date},
        {
            "$setOnInsert": {
                "user_id": user_id,
                "date": date,
                "tasks": [],
                "created_at": now,
            }
        },
        upsert=True,
    )
    doc = await today_document(db, user_id, now=now)
    return doc or {"user_id": user_id, "date": date, "tasks": [], "created_at": now}


async def generate_daily_tasks_for_user(db, user_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Open today's document. Does not rewrite tasks that are already there."""
    return await ensure_today(db, user_id, now=now)


def _session_tasks(doc: Dict[str, Any], session_id: str) -> List[Dict[str, Any]]:
    return [
        task
        for task in (doc.get("tasks") or [])
        if task.get("source") == "REPORT"
        and task.get("source_session_id") == session_id
        and not task.get("is_deleted")
    ]


async def persist_report_tasks(
    db,
    user_id: str,
    session_id: str,
    raw_tasks: Any,
    *,
    crisis: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Append this session's report tasks once.

    A second request for the same session returns the tasks already stored.
    A write error returns the validated proposals and does not drop the report.
    """
    if crisis or not validate_proposals(raw_tasks, []):
        return {"tasks": [], "task_persistence": "skipped"}
    now = now or datetime.now(timezone.utc)
    try:
        doc = await ensure_today(db, user_id, now=now)
        existing = list(doc.get("tasks") or [])
        already = _session_tasks(doc, session_id)
        if already:
            return {"tasks": [public_task(task) for task in already], "task_persistence": "existing"}
        proposals = validate_proposals(raw_tasks, existing)
        if not proposals:
            return {"tasks": [], "task_persistence": "skipped"}
        created = [
            {
                "id": new_task_id("task"),
                "title": item["title"],
                "description": item["description"],
                "completed": False,
                "is_custom": False,
                "is_deleted": False,
                "source": "REPORT",
                "source_session_id": session_id,
                "created_at": now,
            }
            for item in proposals
        ]
        date = server_today(now)
        await db[COLLECTION].update_one(
            {
                "user_id": user_id,
                "date": date,
                "tasks": {"$not": {"$elemMatch": {"source_session_id": session_id}}},
            },
            {"$push": {"tasks": {"$each": created}}},
        )
        fresh = await today_document(db, user_id, now=now)
        stored = _session_tasks(fresh or {}, session_id)
        if not stored:
            return {"tasks": proposals, "task_persistence": "failed"}
        status = "existing" if len(stored) != len(created) else "saved"
        if any(task.get("id") == created[0]["id"] for task in stored):
            status = "saved"
        return {"tasks": [public_task(task) for task in stored], "task_persistence": status}
    except Exception:
        logger.exception("Could not persist report tasks for user=%s", user_id)
        proposals = validate_proposals(raw_tasks, [])
        return {"tasks": proposals, "task_persistence": "failed"}


async def add_selected_report_task(
    db,
    user_id: str,
    session_id: str,
    raw_task: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Append one report task the person chose. Other proposals are left alone."""
    now = now or datetime.now(timezone.utc)
    try:
        doc = await ensure_today(db, user_id, now=now)
        existing = list(doc.get("tasks") or [])
        proposals = validate_proposals([raw_task], existing)
        if not proposals:
            title = str((raw_task or {}).get("title") or "").strip().lower()
            for task in existing:
                if str(task.get("title") or "").strip().lower() == title and title:
                    return {"task": public_task(task), "task_persistence": "existing"}
            return {"task": None, "task_persistence": "skipped"}
        created = {
            "id": new_task_id("task"),
            "title": proposals[0]["title"],
            "description": proposals[0]["description"],
            "completed": False,
            "is_custom": False,
            "is_deleted": False,
            "source": "REPORT",
            "source_session_id": session_id,
            "created_at": now,
        }
        await db[COLLECTION].update_one(
            {"user_id": user_id, "date": server_today(now)},
            {"$push": {"tasks": {"$each": [created]}}},
        )
        return {"task": public_task(created), "task_persistence": "saved"}
    except Exception:
        logger.exception("Could not add selected report task for user=%s", user_id)
        return {"task": None, "task_persistence": "failed"}


async def list_today(db, user_id: str, *, claimed_user_id: Optional[str] = None) -> Dict[str, Any]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot read another user's tasks")
    doc = await generate_daily_tasks_for_user(db, user_id)
    return {
        "user_id": user_id,
        "date": doc.get("date"),
        "tasks": visible_tasks(list(doc.get("tasks") or [])),
    }


async def complete_task(
    db,
    user_id: str,
    task_id: str,
    *,
    claimed_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot complete another user's task")
    doc = await db[COLLECTION].find_one(
        {
            **identity_query(keys),
            "tasks": {"$elemMatch": {"id": task_id, "is_deleted": {"$ne": True}}},
        }
    )
    if not doc:
        raise LookupError("Task not found")
    tasks = []
    found = None
    for task in doc.get("tasks") or []:
        if task.get("id") == task_id and not task.get("is_deleted"):
            task = {**task, "completed": True}
            found = task
        tasks.append(task)
    if found is None:
        raise LookupError("Task not found")
    await db[COLLECTION].update_one({"_id": doc.get("_id"), "user_id": doc.get("user_id")}, {"$set": {"tasks": tasks}})
    return public_task(found)


async def add_custom_task(
    db,
    user_id: str,
    *,
    title: str,
    description: str = "",
    claimed_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot create a task for another user")
    cleaned = validate_proposals([{"title": title, "description": description}], [])
    if not cleaned:
        raise ValueError("Task title is not usable")
    now = now or datetime.now(timezone.utc)
    doc = await ensure_today(db, user_id, now=now)
    task = {
        "id": new_task_id("custom"),
        "title": cleaned[0]["title"],
        "description": cleaned[0]["description"],
        "completed": False,
        "is_custom": True,
        "is_deleted": False,
        "source": "CUSTOM",
        "created_at": now,
    }
    tasks = list(doc.get("tasks") or [])
    tasks.append(task)
    await db[COLLECTION].update_one(
        {"user_id": user_id, "date": doc.get("date")},
        {"$set": {"tasks": tasks}},
    )
    return public_task(task)


async def patch_custom_task(
    db,
    user_id: str,
    task_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    is_deleted: Optional[bool] = None,
    claimed_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot change another user's task")
    doc = await db[COLLECTION].find_one(
        {**identity_query(keys), "tasks": {"$elemMatch": {"id": task_id}}}
    )
    if not doc:
        raise LookupError("Task not found")
    updated = None
    tasks = []
    for task in doc.get("tasks") or []:
        if task.get("id") != task_id:
            tasks.append(task)
            continue
        if not task.get("is_custom"):
            raise ValueError("Only a custom task can be edited here")
        task = dict(task)
        if title is not None:
            checked = validate_proposals([{"title": title, "description": task.get("description") or ""}], [])
            if not checked:
                raise ValueError("Task title is not usable")
            task["title"] = checked[0]["title"]
        if description is not None:
            if len(description) > 240:
                raise ValueError("Description is too long")
            task["description"] = description.strip()
        if is_deleted is not None:
            task["is_deleted"] = bool(is_deleted)
        updated = task
        tasks.append(task)
    if updated is None:
        raise LookupError("Task not found")
    await db[COLLECTION].update_one({"_id": doc.get("_id")}, {"$set": {"tasks": tasks}})
    return public_task(updated)

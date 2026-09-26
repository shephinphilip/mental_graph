"""Writes and reads session_reports. Chat does not create these rows."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from reports.indexes import COLLECTION
from reports.models import (
    clamp_metric,
    merge_events,
    merge_tasks,
    normalize_events,
    normalize_proposed_tasks,
)
from tasks.identity import identity_keys, identity_query, owns_claimed_id
from tasks.store import add_selected_report_task

logger = logging.getLogger(__name__)


def _summary(value: Any) -> str:
    text = str(value or "").strip()
    return text[:1200]


async def recent_reports(db, user_id: str, *, limit: int = 3) -> List[Dict[str, Any]]:
    keys = await identity_keys(db, user_id)
    cursor = (
        db[COLLECTION]
        .find(identity_query(keys))
        .sort("created_at", -1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def open_events(db, user_id: str) -> List[Dict[str, Any]]:
    """Unresolved events across recent reports, oldest label first within a report."""
    found: List[Dict[str, Any]] = []
    for report in await recent_reports(db, user_id, limit=5):
        for event in report.get("events") or []:
            if event.get("resolved"):
                continue
            if not event.get("event_id") or not event.get("label"):
                continue
            found.append(
                {
                    "event_id": event["event_id"],
                    "label": event["label"],
                    "session_id": report.get("session_id"),
                    "user_id": report.get("user_id"),
                }
            )
    return found


async def save_reading(
    db,
    *,
    user_id: str,
    session_id: str,
    summary: str,
    parsed: Dict[str, Any],
    crisis: bool,
) -> Dict[str, Any]:
    """
    Store the reading used by the next welcome.

    Proposed tasks stay on the report until the person adds one.
    A second reading of the same session keeps settled events settled.
    """
    existing = await db[COLLECTION].find_one({"user_id": user_id, "session_id": session_id}) or {}
    psychiatric_summary = _summary(parsed.get("psychiatric_summary") or summary)
    events = merge_events(
        normalize_events([] if crisis else parsed.get("events")),
        list(existing.get("events") or []),
    )
    tasks = [] if crisis else merge_tasks(
        normalize_proposed_tasks(parsed.get("tasks")),
        list(existing.get("proposed_tasks") or existing.get("tasks") or []),
    )
    metric = clamp_metric(parsed.get("psychiatric_metric"))
    if metric is None:
        metric = clamp_metric(existing.get("psychiatric_metric"))
    return {
        "psychiatric_summary": psychiatric_summary,
        "psychiatric_metric": metric,
        "events": events,
        "proposed_tasks": tasks,
        "task_persistence": "proposed" if tasks else "skipped",
    }


async def mark_event_resolved(db, user_id: str, event_id: str) -> bool:
    now = datetime.now(timezone.utc)
    for report in await recent_reports(db, user_id, limit=5):
        events = []
        changed = False
        for event in report.get("events") or []:
            if event.get("event_id") == event_id and not event.get("resolved"):
                event = {**event, "resolved": True, "resolved_at": now}
                changed = True
            events.append(event)
        if not changed:
            continue
        await db[COLLECTION].update_one(
            {"user_id": report.get("user_id"), "session_id": report.get("session_id")},
            {"$set": {"events": events}},
        )
        return True
    return False


async def accept_proposed_task(
    db,
    user_id: str,
    session_id: str,
    task_id: str,
    *,
    claimed_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Copy one proposed task into today's task list. Other proposals stay put."""
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot add another user's task")
    report = await db[COLLECTION].find_one({**identity_query(keys), "session_id": session_id})
    if not report:
        raise LookupError("Report not found")
    chosen = None
    for task in report.get("proposed_tasks") or []:
        if task.get("id") == task_id:
            chosen = task
            break
    if not chosen:
        raise LookupError("Task not found")
    if chosen.get("added"):
        return {"task": chosen, "task_persistence": "existing"}
    saved = await add_selected_report_task(
        db,
        user_id,
        session_id,
        {"title": chosen.get("title"), "description": chosen.get("description") or ""},
    )
    if saved.get("task_persistence") not in {"saved", "existing"}:
        return saved
    proposed = []
    for task in report.get("proposed_tasks") or []:
        if task.get("id") == task_id:
            task = {**task, "added": True}
        proposed.append(task)
    await db[COLLECTION].update_one(
        {"user_id": report.get("user_id"), "session_id": report.get("session_id")},
        {"$set": {"proposed_tasks": proposed}},
    )
    chosen = {**chosen, "added": True, "manager_task_id": (saved.get("task") or {}).get("id")}
    return {"task": chosen, "task_persistence": saved.get("task_persistence")}

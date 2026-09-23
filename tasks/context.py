"""Compact recent-task block. Deleted rows are omitted. Internal ids are not sent."""

from __future__ import annotations

import logging

from tasks.store import recent_task_days

logger = logging.getLogger(__name__)

EMPTY_TASK_CONTEXT = "No tasks available."


def format_task_days(docs) -> str:
    lines = ["RECENT TASKS", "Completed and pending are separate. A deleted task is not listed."]
    any_row = False
    for doc in docs:
        visible = [task for task in (doc.get("tasks") or []) if not task.get("is_deleted")]
        if not visible:
            continue
        any_row = True
        lines.append("")
        lines.append(str(doc.get("date") or "unknown date"))
        for task in visible:
            state = "completed" if task.get("completed") else "pending"
            description = str(task.get("description") or "").strip()
            line = f"- {state}: {task.get('title')}"
            if description:
                line += f" — {description}"
            lines.append(line)
    if not any_row:
        return EMPTY_TASK_CONTEXT
    text = "\n".join(lines)
    if "_id" in text or "source_session_id" in text:
        raise RuntimeError("task context leaked internal ids")
    return text


async def build_task_context(db, user_id: str) -> str:
    try:
        return format_task_days(await recent_task_days(db, user_id, days=3))
    except Exception:
        logger.exception("Task context failed for user=%s", user_id)
        return EMPTY_TASK_CONTEXT


async def pending_task_note(db, user_id: str) -> str:
    """Titles already waiting today, so a report does not copy them."""
    try:
        docs = await recent_task_days(db, user_id, days=1)
    except Exception:
        logger.exception("Pending task note failed for user=%s", user_id)
        return "No pending tasks loaded."
    if not docs:
        return "No pending tasks."
    pending = [
        task.get("title")
        for task in (docs[0].get("tasks") or [])
        if not task.get("is_deleted") and not task.get("completed") and task.get("title")
    ]
    if not pending:
        return "No pending tasks."
    return "EXISTING PENDING TASKS\n" + "\n".join(f"- {title}" for title in pending)

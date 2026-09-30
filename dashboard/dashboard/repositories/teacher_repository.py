"""Teacher actions written by the dashboard. Ratings are not stored."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard.constants import TEACHER_ACTIONS, TEACHER_ACTION_CAP
from dashboard.identity import new_id
from dashboard.repositories.dashboard_repository import _find


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def insert_action(db, doc: dict) -> dict:
    record = {
        "action_id": new_id("tac"),
        "created_at": _now(),
        **doc,
    }
    await db[TEACHER_ACTIONS].insert_one(record)
    return record


async def list_actions(db, school_key: str, teacher_id: str) -> list[dict]:
    return await _find(
        db[TEACHER_ACTIONS],
        {"school_key": school_key, "teacher_id": teacher_id},
        {"_id": 0},
        TEACHER_ACTION_CAP,
        ("created_at", -1),
    )

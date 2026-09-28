"""Persisted dashboard report jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from dashboard.constants import REPORTS
from dashboard.identity import new_id
from dashboard.repositories.dashboard_repository import _find


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_report(db, *, school_key: str, owner_user_id: str, spec: dict) -> dict:
    now = _now()
    doc = {
        "report_id": new_id("rep"),
        "school_key": school_key,
        "owner_user_id": owner_user_id,
        "status": "queued",
        "spec": spec,
        "body": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "ready_at": None,
    }
    await db[REPORTS].insert_one(doc)
    return doc


async def get_report(db, school_key: str, report_id: str) -> Optional[dict]:
    return await db[REPORTS].find_one(
        {"school_key": school_key, "report_id": report_id},
        {"_id": 0},
    )


async def list_reports(db, school_key: str, limit: int) -> list[dict]:
    return await _find(
        db[REPORTS],
        {"school_key": school_key},
        {"_id": 0, "body": 0},
        limit,
        ("created_at", -1),
    )


async def save_report(db, school_key: str, report_id: str, fields: dict) -> None:
    fields = {**fields, "updated_at": _now()}
    await db[REPORTS].update_one(
        {"school_key": school_key, "report_id": report_id},
        {"$set": fields},
    )

"""Intervention plans owned by the school. Not meditation helpfulness flags."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException

from dashboard.constants import INTERVENTIONS
from dashboard.events.publisher import publish
from dashboard.identity import new_id
from dashboard.permissions import assignee_allowed, is_active
from dashboard.repositories.dashboard_repository import _find, load_school_users
from dashboard.services.audit import audit
from dashboard.services.notification_service import create_notification
from dashboard.services.paging import paginate


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public(doc: dict) -> dict[str, Any]:
    return {
        "intervention_id": doc.get("intervention_id"),
        "student_id": doc.get("student_id"),
        "title": doc.get("title"),
        "plan": doc.get("plan"),
        "status": doc.get("status"),
        "assignee_user_id": doc.get("assignee_user_id"),
        "created_by": doc.get("created_by"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


async def _assignee(db, actor, assignee_user_id: Optional[str]) -> Optional[str]:
    if not assignee_user_id:
        return None
    people = await load_school_users(db, actor.query_user, actor.school_key)
    match = next((person for person in people if person["user_id"] == assignee_user_id), None)
    if match is None or not assignee_allowed(match) or not is_active(match):
        raise HTTPException(status_code=404, detail="Assignee not found")
    return assignee_user_id


async def create_intervention(db, actor, *, student_id: str, title: str, plan: str, status: str, assignee_user_id: Optional[str]):
    assignee = await _assignee(db, actor, assignee_user_id)
    now = _now()
    doc = {
        "intervention_id": new_id("int"),
        "school_key": actor.school_key,
        "student_id": student_id,
        "title": title,
        "plan": plan,
        "status": status,
        "assignee_user_id": assignee,
        "created_by": actor.user_id,
        "created_at": now,
        "updated_at": now,
    }
    await db[INTERVENTIONS].insert_one(doc)
    await audit(
        db,
        actor=actor,
        action="intervention.create",
        resource_type="intervention",
        resource_id=doc["intervention_id"],
    )
    publish(
        actor.school_key,
        "intervention_updated",
        intervention_id=doc["intervention_id"],
        student_id=student_id,
        status=status,
    )
    if assignee and assignee != actor.user_id:
        await create_notification(
            db,
            actor=actor,
            category="intervention_updated",
            title="Intervention assigned",
            body=title,
            recipient_user_id=assignee,
            resource_type="intervention",
            resource_id=doc["intervention_id"],
        )
    return _public(doc)


async def get_intervention(db, actor, intervention_id: str) -> dict:
    doc = await db[INTERVENTIONS].find_one(
        {"school_key": actor.school_key, "intervention_id": intervention_id},
        {"_id": 0},
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Intervention not found")
    return _public(doc)


async def update_intervention(db, actor, intervention_id: str, patch: dict) -> dict:
    current = await db[INTERVENTIONS].find_one(
        {"school_key": actor.school_key, "intervention_id": intervention_id},
        {"_id": 0},
    )
    if not current:
        raise HTTPException(status_code=404, detail="Intervention not found")
    fields = {key: value for key, value in patch.items() if value is not None}
    if "assignee_user_id" in patch:
        fields["assignee_user_id"] = await _assignee(db, actor, patch.get("assignee_user_id"))
    if not fields:
        raise HTTPException(status_code=400, detail="No changes were provided")
    fields["updated_at"] = _now()
    await db[INTERVENTIONS].update_one(
        {"school_key": actor.school_key, "intervention_id": intervention_id},
        {"$set": fields},
    )
    await audit(
        db,
        actor=actor,
        action="intervention.update",
        resource_type="intervention",
        resource_id=intervention_id,
    )
    publish(
        actor.school_key,
        "intervention_updated",
        intervention_id=intervention_id,
        student_id=current.get("student_id"),
        status=fields.get("status") or current.get("status"),
    )
    current.update(fields)
    return _public(current)


async def list_for_student(db, actor, student_id: str, *, page: int, limit: int, cursor: Optional[str]):
    rows = await _find(
        db[INTERVENTIONS],
        {"school_key": actor.school_key, "student_id": student_id},
        {"_id": 0},
        200,
        ("updated_at", -1),
    )
    chunk, meta = paginate(
        rows,
        limit=limit,
        page=page,
        cursor=cursor,
        key=lambda row: str(row.get("intervention_id") or ""),
    )
    return [_public(row) for row in chunk], meta

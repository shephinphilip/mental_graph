"""Teachers in the signed-in school. Ratings are not invented."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from dashboard.identity import clean_token, subject_id_of, slug
from dashboard.metrics import RATING_REASON, TEACHER_PERFORMANCE_REASON
from dashboard.services.audit import audit
from dashboard.services.compute import _teacher_card
from dashboard.services.data import get_prepared
from dashboard.services.notification_service import create_notification
from dashboard.services.paging import paginate
from dashboard.repositories.teacher_repository import insert_action, list_actions
from dashboard.metrics import Scope


def _matches(teacher: dict, *, subject_id: Optional[str], class_id: Optional[str]) -> bool:
    if subject_id:
        subjects = {subject_id_of(item) for item in teacher.get("subjects") or []}
        if subject_id not in subjects:
            return False
    if class_id:
        classes = {slug(item) for item in teacher.get("classes") or []}
        if class_id not in classes:
            return False
    return True


async def list_teachers(db, actor, scope, *, subject_id, class_id, page, limit, cursor):
    prepared = await get_prepared(db, actor, scope)
    rows = [
        _teacher_card(teacher)
        for teacher in prepared.teachers
        if _matches(teacher, subject_id=subject_id, class_id=class_id)
    ]
    chunk, meta = paginate(rows, limit=limit, page=page, cursor=cursor, key=lambda row: row["teacher_id"])
    return chunk, meta


async def require_teacher(db, actor, teacher_id: str) -> dict:
    try:
        teacher_id = clean_token(teacher_id, label="teacher_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    prepared = await get_prepared(db, actor, Scope())
    match = next((teacher for teacher in prepared.teachers if teacher["user_id"] == teacher_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return match


async def teacher_profile(db, actor, teacher_id: str) -> dict:
    teacher = await require_teacher(db, actor, teacher_id)
    actions = await list_actions(db, actor.school_key, teacher["user_id"])
    card = _teacher_card(teacher)
    card["actions"] = [
        {
            "action_id": action.get("action_id"),
            "kind": action.get("kind"),
            "body": action.get("body"),
            "focus_areas": action.get("focus_areas") or [],
            "created_at": action.get("created_at"),
            "created_by": action.get("created_by"),
            "rating": None,
            "rating_available": False,
        }
        for action in actions
    ]
    card["rating_reason"] = RATING_REASON
    card["performance_reason"] = TEACHER_PERFORMANCE_REASON
    return card


async def _write(db, actor, teacher_id: str, *, kind: str, body: str, focus_areas: list[str] | None = None):
    teacher = await require_teacher(db, actor, teacher_id)
    action = await insert_action(
        db,
        {
            "school_key": actor.school_key,
            "teacher_id": teacher["user_id"],
            "kind": kind,
            "body": body,
            "focus_areas": focus_areas or [],
            "created_by": actor.user_id,
            "rating": None,
        },
    )
    await create_notification(
        db,
        actor=actor,
        category="teacher_message" if kind == "message" else kind,
        title=f"Teacher {kind.replace('_', ' ')}",
        body=body,
        recipient_user_id=teacher["user_id"],
        resource_type="teacher",
        resource_id=teacher["user_id"],
    )
    await audit(
        db,
        actor=actor,
        action=f"teacher.{kind}",
        resource_type="teacher",
        resource_id=teacher["user_id"],
    )
    return {
        "action_id": action["action_id"],
        "teacher_id": teacher["user_id"],
        "kind": kind,
        "rating": None,
        "rating_available": False,
        "rating_reason": RATING_REASON,
    }


async def send_message(db, actor, teacher_id: str, message: str):
    return await _write(db, actor, teacher_id, kind="message", body=message)


async def add_review(db, actor, teacher_id: str, notes: str, focus_areas: list[str]):
    return await _write(db, actor, teacher_id, kind="review", body=notes, focus_areas=focus_areas)


async def add_support_plan(db, actor, teacher_id: str, summary: str, focus_areas: list[str]):
    return await _write(db, actor, teacher_id, kind="support_plan", body=summary, focus_areas=focus_areas)

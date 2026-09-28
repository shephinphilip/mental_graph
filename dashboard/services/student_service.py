"""Student profile for school staff. Journals and conversations stay out."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from dashboard.identity import clean_token, same_school
from dashboard.metrics import (
    ASSIGNMENT_REASON,
    ATTENDANCE_REASON,
    is_number,
    mean,
    mental_index,
    risk_for_student,
    sleep_hours,
    summarize_student_marks,
    trend_label,
)
from dashboard.permissions import is_active, is_student_account
from dashboard.repositories.dashboard_repository import _public_user, load_attendance
from dashboard.repositories.student_repository import load_student_signals
from dashboard.services.audit import audit
from dashboard.services.intervention_service import list_for_student
from dashboard.services.notification_service import create_notification
from dashboard.identity import isoformat
from dashboard.services.data import get_bundle
from dashboard.metrics import Scope
from services.users import get_by_identifier


async def require_student(db, actor, student_id: str) -> dict:
    try:
        student_id = clean_token(student_id, label="student_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = await get_by_identifier(db, student_id)
    if (
        not isinstance(doc, dict)
        or not is_active(doc)
        or not is_student_account(doc)
        or not same_school(actor.school_key, doc)
        or str(doc.get("user_id") or "") != student_id
    ):
        raise HTTPException(status_code=404, detail="Student not found")
    public = _public_user(doc)
    public["concern_recorded"] = bool(str(doc.get("chief_concern") or "").strip())
    return public


def _activity(signals: dict) -> list[dict]:
    events = []
    for row in signals["moods"][:1]:
        events.append({"kind": "mood_check_in", "at": isoformat(row.get("logged_at") or row.get("created_at"))})
    if signals["marks"]:
        last = signals["marks"][-1]
        events.append({"kind": "assessment", "at": isoformat(last.get("exam_date")), "subject": last.get("subject")})
    for row in signals["meditations"]:
        if str(row.get("status") or "").upper() == "COMPLETED":
            events.append({"kind": "meditation_completed", "at": isoformat(row.get("completed_at"))})
            break
    for row in signals["sleep"][:1]:
        events.append({"kind": "sleep_log", "at": isoformat(row.get("created_at") or row.get("date"))})
    return [event for event in events if event.get("at")]


async def profile(db, actor, student_id: str) -> dict[str, Any]:
    student = await require_student(db, actor, student_id)
    signals = await load_student_signals(db, student["user_id"])
    academic = summarize_student_marks(signals["marks"])
    from config.config import get_settings

    risk = risk_for_student(
        student,
        signals["risk_turns"][0] if signals["risk_turns"] else None,
        signals["patterns"],
        min_confidence=float(get_settings().PATTERN_RETRIEVAL_MIN_CONFIDENCE),
    )
    scores = [float(row["score"]) for row in signals["moods"] if is_number(row.get("score"))]
    hours = [value for value in (sleep_hours(row) for row in signals["sleep"]) if value is not None]
    attendance = student.get("attendance_percentage")
    if attendance is None:
        stored = await load_attendance(db, [student["user_id"]])
        attendance = stored.get(student["user_id"])
    concerns = []
    for pattern in signals["patterns"]:
        status = str(pattern.get("status") or "")
        if status not in {"EMERGING", "ESTABLISHED"}:
            continue
        concerns.append(
            {
                "category": pattern.get("pattern_type"),
                "domains": list(pattern.get("domains") or []),
                "last_observed_at": isoformat(pattern.get("last_observed_at")),
            }
        )
    return {
        "student": {
            "student_id": student["user_id"],
            "name": student.get("name") or "",
            "email": student.get("email"),
            "age": student.get("age"),
            "class_id": student.get("class_id"),
            "class_label": student.get("class_label"),
            "grade_id": student.get("grade_id"),
            "board": student.get("board"),
        },
        "academic": {
            "latest_percentage": academic["latest"],
            "trend": trend_label(academic["delta"], samples=2 if academic["delta"] is not None else 0),
            "growth_delta": academic["delta"],
            "subjects": [
                {
                    "subject": name,
                    "subject_id": item["subject_id"],
                    "latest_percentage": item["latest"],
                    "growth_delta": item["delta"],
                    "trend": item["trend"],
                }
                for name, item in sorted(academic["subjects"].items())
            ],
        },
        "attendance": {
            "value": attendance,
            "available": attendance is not None,
            "reason": None if attendance is not None else ATTENDANCE_REASON,
        },
        "wellbeing": {
            "mood_index": mental_index(scores),
            "mood_samples": len(scores),
            "average_sleep_hours": mean(hours),
            "sleep_samples": len(hours),
        },
        "assignment_completion": None,
        "assignment_completion_reason": ASSIGNMENT_REASON,
        "risk": risk,
        "concerns": concerns[:8],
        "concern_recorded": student["concern_recorded"],
        "recent_activity": _activity(signals),
        "parent_on_file": bool(student.get("parent_on_file")),
    }


async def interventions(db, actor, student_id: str, *, page: int, limit: int, cursor):
    student = await require_student(db, actor, student_id)
    return await list_for_student(
        db, actor, student["user_id"], page=page, limit=limit, cursor=cursor
    )


async def record_parent_contact(db, actor, student_id: str, message: str, reason: Optional[str]):
    student = await require_student(db, actor, student_id)
    note = await create_notification(
        db,
        actor=actor,
        category="parent_contact_recorded",
        title="Parent contact recorded",
        body=message,
        recipient_user_id=actor.user_id,
        resource_type="student",
        resource_id=student["user_id"],
    )
    await audit(
        db,
        actor=actor,
        action="student.parent_contact",
        resource_type="student",
        resource_id=student["user_id"],
    )
    return {
        "contact_id": note["notification_id"],
        "student_id": student["user_id"],
        "status": "recorded",
        "delivered": False,
        "delivery": {
            "available": False,
            "reason": "No parent messaging provider is configured. The request is stored in the school audit trail.",
        },
        "parent_on_file": bool(student.get("parent_on_file")),
        "reason": reason,
    }


async def notify_counselor(db, actor, student_id: str, message: str):
    student = await require_student(db, actor, student_id)
    bundle = await get_bundle(db, actor, Scope())
    counselors = [
        person
        for person in bundle.staff
        if "counselor" in set(person.get("roles") or []) and person["user_id"] != actor.user_id
    ]
    notified = []
    for counselor in counselors:
        note = await create_notification(
            db,
            actor=actor,
            category="counselor_request",
            title="Counselor requested",
            body=message,
            recipient_user_id=counselor["user_id"],
            resource_type="student",
            resource_id=student["user_id"],
        )
        notified.append(note["notification_id"])
    await create_notification(
        db,
        actor=actor,
        category="counselor_request_sent",
        title="Counselor request sent",
        body=message,
        recipient_user_id=actor.user_id,
        resource_type="student",
        resource_id=student["user_id"],
    )
    await audit(
        db,
        actor=actor,
        action="student.notify_counselor",
        resource_type="student",
        resource_id=student["user_id"],
    )
    return {
        "student_id": student["user_id"],
        "counselors_notified": len(notified),
        "notification_ids": notified,
        "status": "sent" if notified else "recorded_no_counselor",
    }

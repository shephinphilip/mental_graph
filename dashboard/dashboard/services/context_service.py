"""School context derived from the signed-in account and live rosters."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from dashboard.identity import calendar_academic_year, mark_year, subject_id_of, subject_name
from dashboard.permissions import PERMISSIONS, scrub_roles
from dashboard.services.data import get_bundle
from dashboard.metrics import Scope


async def context_payload(db, actor) -> dict[str, Any]:
    bundle = await get_bundle(db, actor, Scope())
    boards = [student.get("board") for student in bundle.students if student.get("board")]
    return {
        "user": {
            "user_id": actor.user_id,
            "name": actor.name,
            "email": actor.email,
        },
        "school": {
            "id": actor.school_key,
            "name": actor.school_name or None,
            "board": actor.board or (boards[0] if boards else None),
        },
        "role": actor.primary_role,
        "roles": scrub_roles(actor.roles),
        "permissions": list(PERMISSIONS),
    }


async def academic_years(db, actor) -> dict[str, Any]:
    bundle = await get_bundle(db, actor, Scope())
    found = []
    seen = set()
    for mark in bundle.marks:
        year = mark_year(mark)
        if year and year not in seen:
            seen.add(year)
            found.append({"id": year, "source": "marks"})
    found.sort(key=lambda item: item["id"])
    calendar = calendar_academic_year()
    return {
        "years": found,
        "calendar_year": {"id": calendar, "source": "calendar", "in_marks": calendar in seen},
    }


async def grades(db, actor) -> dict[str, Any]:
    bundle = await get_bundle(db, actor, Scope())
    counts: dict[str, int] = defaultdict(int)
    for student in bundle.students:
        counts[student["grade_id"]] += 1
    return {
        "grades": [
            {"grade_id": grade_id, "student_count": count}
            for grade_id, count in sorted(counts.items())
        ]
    }


async def classes(db, actor, *, grade_id: str | None) -> dict[str, Any]:
    bundle = await get_bundle(db, actor, Scope())
    grouped: dict[str, dict] = {}
    for student in bundle.students:
        if grade_id and student["grade_id"] != grade_id:
            continue
        slot = grouped.setdefault(
            student["class_id"],
            {
                "class_id": student["class_id"],
                "label": student.get("class_label") or student["class_id"],
                "grade_id": student["grade_id"],
                "student_count": 0,
            },
        )
        slot["student_count"] += 1
    return {"classes": sorted(grouped.values(), key=lambda item: item["class_id"])}


async def subjects(db, actor, *, grade_id: str | None, class_id: str | None) -> dict[str, Any]:
    bundle = await get_bundle(db, actor, Scope(grade_id=grade_id, class_id=class_id))
    allowed = {
        student["user_id"]
        for student in bundle.students
        if (not grade_id or student["grade_id"] == grade_id)
        and (not class_id or student["class_id"] == class_id)
    }
    found: dict[str, str] = {}
    for mark in bundle.marks:
        if str(mark.get("student_id") or "") not in allowed:
            continue
        name = subject_name(mark.get("subject"))
        if not name:
            continue
        found[subject_id_of(name)] = name
    return {
        "subjects": [
            {"subject_id": subject_id, "name": name}
            for subject_id, name in sorted(found.items(), key=lambda item: item[1].lower())
        ]
    }

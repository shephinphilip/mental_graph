"""Bounded reads. One query per collection, then in-memory metrics.

Aggregation is used for the latest risk turn per student when the driver
implements ``aggregate``. The metric formulas themselves stay in
``dashboard.metrics`` so tests and production cannot drift.
"""

from __future__ import annotations

from typing import Any, Optional

from dashboard.constants import (
    MARKS,
    MAX_MARKS,
    MAX_SIGNALS,
    MAX_USERS,
    MEDITATIONS,
    MOODS,
    PATTERNS,
    PROFILES,
    RISK_TURNS,
    SLEEP,
    USERS,
)
from dashboard.identity import (
    class_id_of,
    class_label,
    grade_id_of,
    listed,
    school_query,
    same_school,
    subject_id_of,
)
from dashboard.metrics import attendance_percentage
from dashboard.permissions import is_active, is_student_account, is_teacher_account, role_set


async def _collect(cursor, limit: int) -> list[dict]:
    if hasattr(cursor, "limit"):
        limited = cursor.limit(limit)
        cursor = limited or cursor
    if hasattr(cursor, "to_list"):
        rows = await cursor.to_list(length=limit)
        return list(rows or [])
    return []


async def _find(collection, query: dict, projection: dict | None, limit: int, sort: tuple | None = None) -> list[dict]:
    cursor = collection.find(query, projection)
    if sort is not None and hasattr(cursor, "sort"):
        cursor = cursor.sort([(sort[0], sort[1])]) or cursor
    return await _collect(cursor, limit)


async def _find_in(
    collection,
    field: str,
    ids: list[str],
    projection: dict,
    limit: int,
    sort: tuple | None = None,
) -> tuple[list[dict], bool]:
    if not ids:
        return [], False
    found: list[dict] = []
    truncated = False
    for start in range(0, len(ids), 500):
        if len(found) >= limit:
            truncated = True
            break
        chunk = ids[start : start + 500]
        rows = await _find(
            collection,
            {field: {"$in": chunk}},
            projection,
            limit - len(found),
            sort,
        )
        found.extend(rows)
        if len(rows) >= limit - (len(found) - len(rows)):
            truncated = len(found) >= limit
    return found[:limit], truncated or len(found) >= limit


def _public_user(doc: dict) -> dict:
    subjects = listed(doc.get("subjects") or doc.get("subject"))
    classes = listed(doc.get("classes") or doc.get("class_ids"))
    parent = bool(doc.get("parent_email") or doc.get("parent_phone") or doc.get("guardian_email"))
    shaped = {
        "user_id": str(doc.get("user_id") or ""),
        "name": doc.get("name") or "",
        "email": doc.get("email"),
        "class": doc.get("class"),
        "grade": doc.get("grade"),
        "class_level": doc.get("class_level"),
        "school": doc.get("school"),
        "school_id": doc.get("school_id"),
        "roles": sorted(role_set(doc)),
        "isActive": doc.get("isActive", True),
        "current_risk_level": doc.get("current_risk_level"),
        "board": doc.get("board") or doc.get("school_board"),
        "attendance_percentage": doc.get("attendance_percentage"),
        "attendance": doc.get("attendance") if isinstance(doc.get("attendance"), (int, float, dict)) else None,
        "subjects": subjects,
        "classes": classes,
        "parent_on_file": parent,
        "age": doc.get("age") if isinstance(doc.get("age"), int) else None,
    }
    shaped["class_label"] = class_label(shaped)
    shaped["class_id"] = class_id_of(shaped)
    shaped["grade_id"] = grade_id_of(shaped)
    shaped["attendance_percentage"] = attendance_percentage(shaped)
    shaped.pop("attendance", None)
    return shaped


async def load_school_users(db, actor_user: dict, school_key: str) -> list[dict]:
    rows = await _find(
        db[USERS],
        school_query(actor_user),
        {
            "user_id": 1,
            "name": 1,
            "email": 1,
            "class": 1,
            "grade": 1,
            "class_level": 1,
            "school": 1,
            "school_id": 1,
            "roles": 1,
            "isActive": 1,
            "current_risk_level": 1,
            "board": 1,
            "school_board": 1,
            "attendance_percentage": 1,
            "attendance": 1,
            "subjects": 1,
            "subject": 1,
            "classes": 1,
            "class_ids": 1,
            "parent_email": 1,
            "parent_phone": 1,
            "guardian_email": 1,
            "age": 1,
            "_id": 0,
        },
        MAX_USERS,
    )
    people = []
    for row in rows:
        if not is_active(row) or not same_school(school_key, row):
            continue
        people.append(_public_user(row))
    return people


def partition(people: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    students = [person for person in people if is_student_account(person)]
    teachers = [person for person in people if is_teacher_account(person)]
    staff = [person for person in people if not is_student_account(person)]
    return students, teachers, staff


async def latest_risk_turns(db, student_ids: list[str]) -> tuple[list[dict], bool]:
    if not student_ids:
        return [], False
    collection = db[RISK_TURNS]
    aggregate = getattr(collection, "aggregate", None)
    if aggregate is not None:
        try:
            cursor = aggregate(
                [
                    {"$match": {"user_id": {"$in": student_ids}}},
                    {"$sort": {"created_at": -1}},
                    {
                        "$group": {
                            "_id": "$user_id",
                            "risk_intensity_score": {"$first": "$risk_intensity_score"},
                            "crisis_keywords": {"$first": "$crisis_keywords"},
                            "created_at": {"$first": "$created_at"},
                            "band": {"$first": "$band"},
                        }
                    },
                ]
            )
            rows = await cursor.to_list(length=max(len(student_ids), 1))
            return [
                {
                    "user_id": row.get("_id"),
                    "risk_intensity_score": row.get("risk_intensity_score"),
                    "crisis_keywords": row.get("crisis_keywords"),
                    "created_at": row.get("created_at"),
                    "band": row.get("band"),
                }
                for row in rows
                if row.get("_id")
            ], False
        except Exception:
            pass
    rows, truncated = await _find_in(
        collection,
        "user_id",
        student_ids,
        {
            "user_id": 1,
            "risk_intensity_score": 1,
            "crisis_keywords": 1,
            "created_at": 1,
            "band": 1,
            "_id": 0,
        },
        MAX_SIGNALS,
        ("created_at", -1),
    )
    latest: dict[str, dict] = {}
    for row in rows:
        user_id = str(row.get("user_id") or "")
        if user_id and user_id not in latest:
            latest[user_id] = row
    return list(latest.values()), truncated


async def load_attendance(db, student_ids: list[str]) -> dict[str, float]:
    if not student_ids:
        return {}
    rows, _ = await _find_in(
        db[PROFILES],
        "user_id",
        student_ids,
        {"user_id": 1, "attendance.attendance_percentage": 1, "_id": 0},
        MAX_USERS,
    )
    found: dict[str, float] = {}
    for row in rows:
        user_id = str(row.get("user_id") or "")
        attendance = row.get("attendance") if isinstance(row.get("attendance"), dict) else {}
        value = attendance.get("attendance_percentage")
        if user_id and isinstance(value, (int, float)) and not isinstance(value, bool):
            found[user_id] = float(value)
    return found


class SchoolBundle:
    def __init__(
        self,
        *,
        students: list[dict],
        teachers: list[dict],
        staff: list[dict],
        marks: list[dict],
        moods: list[dict],
        sleep: list[dict],
        patterns: list[dict],
        risk_turns: list[dict],
        meditations: list[dict],
        profile_attendance: dict[str, float],
        truncated: dict[str, bool],
    ) -> None:
        self.students = students
        self.teachers = teachers
        self.staff = staff
        self.marks = marks
        self.moods = moods
        self.sleep = sleep
        self.patterns = patterns
        self.risk_turns = risk_turns
        self.meditations = meditations
        self.profile_attendance = profile_attendance
        self.truncated = truncated

    def by_student(self) -> dict[str, dict]:
        return {student["user_id"]: student for student in self.students}


async def load_bundle(db, actor_user: dict, school_key: str) -> SchoolBundle:
    people = await load_school_users(db, actor_user, school_key)
    students, teachers, staff = partition(people)
    ids = [student["user_id"] for student in students if student.get("user_id")]
    marks, marks_truncated = await _find_in(
        db[MARKS],
        "student_id",
        ids,
        {
            "student_id": 1,
            "subject": 1,
            "percentage": 1,
            "marks": 1,
            "total_marks": 1,
            "exam_date": 1,
            "exam_type": 1,
            "academic_year": 1,
            "_id": 0,
        },
        MAX_MARKS,
        ("exam_date", 1),
    )
    moods, moods_truncated = await _find_in(
        db[MOODS],
        "user_id",
        ids,
        {"user_id": 1, "score": 1, "mood": 1, "logged_at": 1, "created_at": 1, "_id": 0},
        MAX_SIGNALS,
        ("logged_at", -1),
    )
    sleep, sleep_truncated = await _find_in(
        db[SLEEP],
        "user_id",
        ids,
        {"user_id": 1, "total_duration_minutes": 1, "date": 1, "created_at": 1, "_id": 0},
        MAX_SIGNALS,
        ("created_at", -1),
    )
    patterns, patterns_truncated = await _find_in(
        db[PATTERNS],
        "user_id",
        ids,
        {
            "user_id": 1,
            "pattern_id": 1,
            "pattern_type": 1,
            "domains": 1,
            "status": 1,
            "confidence": 1,
            "last_observed_at": 1,
            "_id": 0,
        },
        MAX_SIGNALS,
        ("last_observed_at", -1),
    )
    turns, turns_truncated = await latest_risk_turns(db, ids)
    meditations, meditation_truncated = await _find_in(
        db[MEDITATIONS],
        "user_id",
        ids,
        {"user_id": 1, "status": 1, "completed_at": 1, "meditation_id": 1, "_id": 0},
        MAX_SIGNALS,
        ("completed_at", -1),
    )
    attendance = await load_attendance(db, ids)
    allowed = set(ids)
    return SchoolBundle(
        students=students,
        teachers=teachers,
        staff=staff,
        marks=[row for row in marks if str(row.get("student_id") or "") in allowed],
        moods=[row for row in moods if str(row.get("user_id") or "") in allowed],
        sleep=[row for row in sleep if str(row.get("user_id") or "") in allowed],
        patterns=[row for row in patterns if str(row.get("user_id") or "") in allowed],
        risk_turns=[row for row in turns if str(row.get("user_id") or "") in allowed],
        meditations=[row for row in meditations if str(row.get("user_id") or "") in allowed],
        profile_attendance={key: value for key, value in attendance.items() if key in allowed},
        truncated={
            "users": len(people) >= MAX_USERS,
            "marks": marks_truncated,
            "moods": moods_truncated,
            "sleep": sleep_truncated,
            "patterns": patterns_truncated,
            "risk_turns": turns_truncated,
            "meditations": meditation_truncated,
        },
    )


def group_by(rows: list[dict], field: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        key = str(row.get(field) or "")
        if not key:
            continue
        grouped.setdefault(key, []).append(row)
    return grouped

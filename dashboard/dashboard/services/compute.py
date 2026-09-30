"""Turn a school bundle into dashboard views. No database calls."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config.config import get_settings

from dashboard.constants import ATTENTION_CAP, QUADRANT_STUDENT_CAP, TOP_PERFORMER_CAP
from dashboard.identity import as_datetime, slug, subject_id_of
from dashboard.metrics import (
    week_key,
    ASSIGNMENT_REASON,
    ATTENDANCE_REASON,
    BENCHMARK_REASON,
    CONCEPT_REASON,
    PARENT_NPS_REASON,
    PASS_RATE_REASON,
    QUADRANT_METHOD,
    RATING_REASON,
    SGP_REASON,
    SOCIAL_REASON,
    STRENGTH_PERCENTAGE,
    STRUGGLE_PERCENTAGE,
    TEACHER_PERFORMANCE_REASON,
    Scope,
    build_quadrant,
    classify_quadrant,
    composite,
    insight_text,
    is_number,
    mark_in_scope,
    mean,
    mental_index,
    mood_delta,
    physical_index,
    risk_for_student,
    signal_in_scope,
    sleep_hours,
    student_matches,
    summarize_student_marks,
    trend_label,
    unavailable,
)
from dashboard.repositories.dashboard_repository import SchoolBundle, group_by
from backend_core.marks import stored_percentage


class Prepared:
    def __init__(self) -> None:
        self.students: list[dict] = []
        self.teachers: list[dict] = []
        self.staff: list[dict] = []
        self.marks: list[dict] = []
        self.moods: list[dict] = []
        self.sleep: list[dict] = []
        self.patterns: dict[str, list[dict]] = {}
        self.turns: dict[str, dict] = {}
        self.meditations: list[dict] = []
        self.academics: dict[str, dict] = {}
        self.risks: dict[str, dict] = {}
        self.rows: list[dict] = []
        self.truncated: dict[str, bool] = {}


def _min_confidence() -> float:
    return float(get_settings().PATTERN_RETRIEVAL_MIN_CONFIDENCE)


def prepare(bundle: SchoolBundle, scope: Scope) -> Prepared:
    prepared = Prepared()
    prepared.students = [row for row in bundle.students if student_matches(row, scope)]
    prepared.teachers = list(bundle.teachers)
    prepared.staff = list(bundle.staff)
    prepared.truncated = dict(bundle.truncated)
    index = {row["user_id"]: row for row in prepared.students}
    prepared.marks = [
        mark
        for mark in bundle.marks
        if mark_in_scope(mark, index.get(str(mark.get("student_id") or "")), scope)
    ]
    prepared.moods = [
        row
        for row in bundle.moods
        if signal_in_scope(row, index.get(str(row.get("user_id") or "")), scope, "logged_at")
        or signal_in_scope(row, index.get(str(row.get("user_id") or "")), scope, "created_at")
    ]
    # signal_in_scope is true when the chosen field is in range. A mood with only
    # created_at should still count. The `or` above double-counts rows that have
    # both timestamps. Dedupe by identity.
    seen = set()
    moods = []
    for row in bundle.moods:
        student = index.get(str(row.get("user_id") or ""))
        when_ok = signal_in_scope(row, student, scope, "logged_at") or signal_in_scope(
            row, student, scope, "created_at"
        )
        if not when_ok:
            continue
        marker = id(row)
        if marker in seen:
            continue
        seen.add(marker)
        moods.append(row)
    prepared.moods = moods
    prepared.sleep = [
        row
        for row in bundle.sleep
        if signal_in_scope(row, index.get(str(row.get("user_id") or "")), scope, "created_at")
        or signal_in_scope(row, index.get(str(row.get("user_id") or "")), scope, "date")
    ]
    sleep_seen = set()
    sleep_rows = []
    for row in bundle.sleep:
        student = index.get(str(row.get("user_id") or ""))
        if not (
            signal_in_scope(row, student, scope, "created_at")
            or signal_in_scope(row, student, scope, "date")
        ):
            continue
        marker = id(row)
        if marker in sleep_seen:
            continue
        sleep_seen.add(marker)
        sleep_rows.append(row)
    prepared.sleep = sleep_rows
    pattern_groups = group_by(
        [
            row
            for row in bundle.patterns
            if student_matches(index.get(str(row.get("user_id") or ""), {}), scope)
            and str(row.get("user_id") or "") in index
        ],
        "user_id",
    )
    prepared.patterns = pattern_groups
    prepared.turns = {}
    for row in bundle.risk_turns:
        user_id = str(row.get("user_id") or "")
        if user_id in index:
            prepared.turns[user_id] = row
    prepared.meditations = [
        row
        for row in bundle.meditations
        if str(row.get("user_id") or "") in index
        and signal_in_scope(row, index.get(str(row.get("user_id") or "")), scope, "completed_at")
    ]
    marks_by = group_by(prepared.marks, "student_id")
    floor = _min_confidence()
    for student in prepared.students:
        user_id = student["user_id"]
        academic = summarize_student_marks(marks_by.get(user_id, []))
        prepared.academics[user_id] = academic
        risk = risk_for_student(
            student,
            prepared.turns.get(user_id),
            prepared.patterns.get(user_id, []),
            min_confidence=floor,
        )
        prepared.risks[user_id] = risk
        attendance = student.get("attendance_percentage")
        if attendance is None:
            attendance = bundle.profile_attendance.get(user_id)
        quadrant = classify_quadrant(academic["latest"], academic["delta"])
        prepared.rows.append(
            {
                "student_id": user_id,
                "name": student.get("name") or "",
                "class_id": student.get("class_id"),
                "grade_id": student.get("grade_id"),
                "class_label": student.get("class_label") or "",
                "latest": academic["latest"],
                "delta": academic["delta"],
                "quadrant": quadrant,
                "severity": risk["severity"],
                "concern": risk["concern"],
                "last_event_at": risk["last_event_at"],
                "attendance": attendance,
                "subjects": academic["subjects"],
            }
        )
    return prepared


def _risk_counts(rows: list[dict]) -> dict[str, int]:
    counts = {"total_students": len(rows), "critical": 0, "at_risk": 0, "watch": 0, "none": 0}
    for row in rows:
        severity = row.get("severity")
        if severity in {"critical", "at_risk", "watch"}:
            counts[severity] += 1
        else:
            counts["none"] += 1
    return counts


def _mood_scores(moods: list[dict]) -> list[float]:
    return [float(row["score"]) for row in moods if is_number(row.get("score"))]


def _hours(sleep_rows: list[dict]) -> list[float]:
    values = []
    for row in sleep_rows:
        hours = sleep_hours(row)
        if hours is not None:
            values.append(hours)
    return values


def _subject_table(rows: list[dict]) -> list[dict]:
    bucket: dict[str, dict[str, Any]] = {}
    for row in rows:
        for name, item in (row.get("subjects") or {}).items():
            slot = bucket.setdefault(
                name,
                {"subject": name, "subject_id": item["subject_id"], "latest": [], "delta": []},
            )
            slot["latest"].append(item["latest"])
            if item["delta"] is not None:
                slot["delta"].append(item["delta"])
    table = []
    for name, slot in sorted(bucket.items(), key=lambda pair: pair[0].lower()):
        average = mean(slot["latest"])
        growth = mean(slot["delta"]) if slot["delta"] else None
        table.append(
            {
                "subject_id": slot["subject_id"],
                "subject": name,
                "average": average,
                "growth": growth,
                "trend": trend_label(growth, samples=2 if growth is not None else 0),
                "students": len(slot["latest"]),
            }
        )
    return table


def _weekly(marks: list[dict]) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for mark in marks:
        when = as_datetime(mark.get("exam_date"))
        pct = stored_percentage(mark)
        if when is None or pct is None:
            continue
        buckets[week_key(when)].append(pct)
    return [
        {"period": period, "average": mean(values), "assessments": len(values)}
        for period, values in sorted(buckets.items())
    ]


def _weekly_moods(moods: list[dict]) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in moods:
        if not is_number(row.get("score")):
            continue
        when = as_datetime(row.get("logged_at") or row.get("created_at"))
        if when is None:
            continue
        buckets[week_key(when)].append(float(row["score"]))
    return [
        {"period": period, "average_score": mean(values), "samples": len(values)}
        for period, values in sorted(buckets.items())
    ]


def _mode_board(students: list[dict]) -> Optional[str]:
    counts: dict[str, int] = defaultdict(int)
    for student in students:
        board = " ".join(str(student.get("board") or "").split())
        if board:
            counts[board] += 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _attention(rows: list[dict]) -> list[dict]:
    chosen = []
    for row in rows:
        reasons = []
        if row.get("severity") in {"critical", "at_risk"}:
            reasons.append("risk")
        if row.get("quadrant") == "critical":
            reasons.append("academic_quadrant")
        if not reasons:
            continue
        chosen.append(
            {
                "student_id": row["student_id"],
                "name": row.get("name") or "",
                "severity": row.get("severity"),
                "reasons": reasons,
                "concern": row.get("concern"),
                "last_event_at": row.get("last_event_at"),
            }
        )
    rank = {"critical": 0, "at_risk": 1, None: 2, "watch": 3}
    chosen.sort(key=lambda item: (rank.get(item.get("severity"), 9), item["name"]))
    return chosen[:ATTENTION_CAP]


def _engagement(rows: list[dict], moods: list[dict], meditations: list[dict]) -> Optional[float]:
    ids = {row["student_id"] for row in rows}
    if not ids:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    active = set()
    for row in moods:
        when = as_datetime(row.get("logged_at") or row.get("created_at"))
        if when and when >= cutoff and str(row.get("user_id") or "") in ids:
            active.add(str(row.get("user_id")))
    for row in meditations:
        if str(row.get("status") or "").upper() != "COMPLETED":
            continue
        when = as_datetime(row.get("completed_at"))
        if when and when >= cutoff and str(row.get("user_id") or "") in ids:
            active.add(str(row.get("user_id")))
    return round(len(active) / len(ids), 3)


def overview_payload(actor, prepared: Prepared) -> dict[str, Any]:
    rows = prepared.rows
    scores = _mood_scores(prepared.moods)
    hours = _hours(prepared.sleep)
    mental = mental_index(scores)
    physical = physical_index(hours)
    health = composite([physical, mental])
    wellness_change = mood_delta(prepared.moods)
    latests = [row["latest"] for row in rows if row["latest"] is not None]
    deltas = [row["delta"] for row in rows if row["delta"] is not None]
    academic_index = mean(latests)
    academic_delta = mean(deltas) if deltas else None
    academic_trend = trend_label(academic_delta, samples=len(deltas))
    attendance_values = [row["attendance"] for row in rows if row["attendance"] is not None]
    attendance_mean = mean(attendance_values)
    risk = _risk_counts(rows)
    subjects = _subject_table(rows)
    weakest = None
    if subjects:
        ranked = [item for item in subjects if item["average"] is not None]
        if ranked:
            weakest = sorted(ranked, key=lambda item: item["average"])[0]["subject"]
    performers = sorted(
        [row for row in rows if row["latest"] is not None],
        key=lambda row: row["latest"],
        reverse=True,
    )[:TOP_PERFORMER_CAP]
    teachers = [_teacher_card(teacher) for teacher in prepared.teachers]
    unavailable_metrics = [
        {"metric": "social_connection", "reason": SOCIAL_REASON},
        {"metric": "passing_rate", "reason": PASS_RATE_REASON},
        {"metric": "parent_nps", "reason": PARENT_NPS_REASON},
        {"metric": "teacher_rating", "reason": RATING_REASON},
        {"metric": "teacher_performance", "reason": TEACHER_PERFORMANCE_REASON},
    ]
    if mental is None:
        unavailable_metrics.append(
            {"metric": "mental_health", "reason": "No numeric mood scores are stored for this scope."}
        )
    if physical is None:
        unavailable_metrics.append(
            {"metric": "physical_health", "reason": "No sleep durations are stored for this scope."}
        )
    if attendance_mean is None:
        unavailable_metrics.append({"metric": "attendance", "reason": ATTENDANCE_REASON})
    return {
        "school": {
            "id": actor.school_key,
            "name": actor.school_name or None,
            "board": actor.board or _mode_board(prepared.students),
        },
        "counts": {"students": len(rows), "teachers": len(prepared.teachers)},
        "school_health": {
            "index": health,
            "trend": trend_label(wellness_change, samples=2 if wellness_change is not None else 0),
            "physical": {
                "value": physical,
                "samples": len(hours),
                "available": physical is not None,
                "reason": None if physical is not None else "No sleep durations are stored for this scope.",
            },
            "mental": {
                "value": mental,
                "samples": len(scores),
                "available": mental is not None,
                "reason": None if mental is not None else "No numeric mood scores are stored for this scope.",
            },
            "social_connection": unavailable("social_connection", SOCIAL_REASON),
        },
        "academic_health": {
            "index": academic_index,
            "mean_percentage": academic_index,
            "students_with_marks": len(latests),
            "passing_rate": None,
            "passing_rate_available": False,
            "passing_rate_reason": PASS_RATE_REASON,
            "mental_health_indicator": mental,
            "attendance": {
                "value": attendance_mean,
                "students_with_records": len(attendance_values),
                "available": attendance_mean is not None,
                "reason": None if attendance_mean is not None else ATTENDANCE_REASON,
            },
        },
        "school_growth": {
            "academics": {
                "delta": academic_delta,
                "trend": academic_trend,
                "students": len(deltas),
            },
            "wellness": {
                "delta": wellness_change,
                "trend": trend_label(wellness_change, samples=2 if wellness_change is not None else 0),
                "available": wellness_change is not None,
                "reason": None
                if wellness_change is not None
                else "Need numeric mood scores in both the last 14 days and the 14 days before that.",
            },
            "parent_nps": unavailable("parent_nps", PARENT_NPS_REASON),
            "trend": academic_trend,
        },
        "risk": risk,
        "top_performers": [
            {
                "student_id": row["student_id"],
                "name": row["name"],
                "latest_percentage": row["latest"],
                "class_id": row["class_id"],
            }
            for row in performers
        ],
        "teachers": teachers,
        "insight": {
            "text": insight_text(risk=risk, academic_trend=academic_trend, weakest_subject=weakest),
            "source": "stored_records",
            "llm_used": False,
        },
        "unavailable": unavailable_metrics,
        "sample_limits": prepared.truncated,
    }


def _teacher_card(teacher: dict) -> dict[str, Any]:
    return {
        "teacher_id": teacher["user_id"],
        "name": teacher.get("name") or "",
        "subjects": list(teacher.get("subjects") or []),
        "classes": list(teacher.get("classes") or []),
        "rating": None,
        "rating_available": False,
        "rating_reason": RATING_REASON,
        "performance": None,
        "performance_reason": TEACHER_PERFORMANCE_REASON,
    }


def class_overview_payload(class_id: str, prepared: Prepared) -> dict[str, Any]:
    rows = [row for row in prepared.rows if row["class_id"] == class_id]
    if not rows:
        return {}
    label = rows[0].get("class_label") or class_id
    grade_id = rows[0].get("grade_id")
    latests = [row["latest"] for row in rows if row["latest"] is not None]
    deltas = [row["delta"] for row in rows if row["delta"] is not None]
    academic_delta = mean(deltas) if deltas else None
    subjects = _subject_table(rows)
    strengths = [item["subject"] for item in subjects if item["average"] is not None and item["average"] >= STRENGTH_PERCENTAGE]
    weaknesses = [item["subject"] for item in subjects if item["average"] is not None and item["average"] < STRUGGLE_PERCENTAGE]
    attendance_values = [row["attendance"] for row in rows if row["attendance"] is not None]
    attendance_mean = mean(attendance_values)
    student_ids = {row["student_id"] for row in rows}
    moods = [row for row in prepared.moods if str(row.get("user_id") or "") in student_ids]
    meditations = [row for row in prepared.meditations if str(row.get("user_id") or "") in student_ids]
    return {
        "class": {
            "id": class_id,
            "label": label,
            "grade_id": grade_id,
            "student_count": len(rows),
        },
        "academic_index": mean(latests),
        "growth_sgp": unavailable("growth_sgp", SGP_REASON),
        "growth_delta": academic_delta,
        "performance_quadrant": {
            key: value["count"] if isinstance(value, dict) else value
            for key, value in build_quadrant(rows, cap=QUADRANT_STUDENT_CAP).items()
        },
        "wellness_trend": _weekly_moods(moods),
        "strengths": strengths,
        "weaknesses": weaknesses,
        "students_requiring_attention": _attention(rows),
        "engagement": {
            "wellness_engagement_rate_14d": _engagement(rows, moods, meditations),
            "assignment_completion": None,
            "assignment_completion_reason": ASSIGNMENT_REASON,
            "attendance": {
                "value": attendance_mean,
                "students_with_records": len(attendance_values),
                "available": attendance_mean is not None,
                "reason": None if attendance_mean is not None else ATTENDANCE_REASON,
            },
        },
    }


def quadrant_payload(class_id: str, prepared: Prepared) -> dict[str, Any]:
    rows = [row for row in prepared.rows if row["class_id"] == class_id]
    board = build_quadrant(rows, cap=QUADRANT_STUDENT_CAP)
    board["method"] = QUADRANT_METHOD
    return board


def subject_overview_payload(class_id: str, subject_id: str, prepared: Prepared) -> Optional[dict]:
    rows = [row for row in prepared.rows if row["class_id"] == class_id]
    if not rows:
        return None
    class_values = []
    needy = []
    subject_label = None
    for row in rows:
        for name, item in (row.get("subjects") or {}).items():
            if item["subject_id"] != subject_id:
                continue
            subject_label = name
            class_values.append(item["latest"])
            if item["latest"] < STRUGGLE_PERCENTAGE or item["trend"] == "declining":
                needy.append(
                    {
                        "student_id": row["student_id"],
                        "name": row["name"],
                        "latest_percentage": item["latest"],
                        "growth_delta": item["delta"],
                        "trend": item["trend"],
                    }
                )
    if subject_label is None:
        return None
    school_values = []
    for row in prepared.rows:
        for name, item in (row.get("subjects") or {}).items():
            if item["subject_id"] == subject_id:
                school_values.append(item["latest"])
    marks = [
        mark
        for mark in prepared.marks
        if subject_id_of(mark.get("subject")) == subject_id
        and str(mark.get("student_id") or "") in {row["student_id"] for row in rows}
    ]
    matched = _teachers_for(prepared.teachers, subject_id, class_id)
    teacher = None
    teacher_note = "No teacher record lists both this subject and this class."
    if len(matched) == 1:
        teacher = _teacher_card(matched[0])
        teacher_note = None
    elif len(matched) > 1:
        teacher_note = "More than one teacher lists this class and subject."
    class_average = mean(class_values)
    return {
        "subject": {"id": subject_id, "name": subject_label},
        "class_id": class_id,
        "teacher": teacher,
        "teacher_note": teacher_note,
        "student_feedback": unavailable("student_feedback", RATING_REASON),
        "class_average": class_average,
        "school_average": mean(school_values),
        "performance_trend": _weekly(marks),
        "concept_mastery": unavailable("concept_mastery", CONCEPT_REASON),
        "strengths": [subject_label] if class_average is not None and class_average >= STRENGTH_PERCENTAGE else [],
        "weaknesses": [subject_label] if class_average is not None and class_average < STRUGGLE_PERCENTAGE else [],
        "micro_topic_health": unavailable("micro_topic_health", CONCEPT_REASON),
        "students_needing_attention": needy[:ATTENTION_CAP],
    }


def _teachers_for(teachers: list[dict], subject_id: str, class_id: str) -> list[dict]:
    found = []
    for teacher in teachers:
        subjects = {subject_id_of(item) for item in teacher.get("subjects") or []}
        classes = {slug(item) for item in teacher.get("classes") or []}
        if subject_id in subjects and class_id in classes:
            found.append(teacher)
    return found


def grade_trends(prepared: Prepared) -> list[dict]:
    by_grade: dict[str, list[dict]] = defaultdict(list)
    index = {row["student_id"]: row for row in prepared.rows}
    for mark in prepared.marks:
        student = index.get(str(mark.get("student_id") or ""))
        if student is None:
            continue
        by_grade[student["grade_id"]].append(mark)
    payload = []
    for grade_id, marks in sorted(by_grade.items()):
        payload.append({"grade_id": grade_id, "periods": _weekly(marks)})
    return payload


def wellbeing_payload(prepared: Prepared) -> dict[str, Any]:
    scores = _mood_scores(prepared.moods)
    hours = _hours(prepared.sleep)
    return {
        "students": len(prepared.rows),
        "mental": {
            "index": mental_index(scores),
            "samples": len(scores),
            "available": bool(scores),
        },
        "physical": {
            "index": physical_index(hours),
            "samples": len(hours),
            "available": bool(hours),
        },
        "social_connection": unavailable("social_connection", SOCIAL_REASON),
        "mood_trend": _weekly_moods(prepared.moods),
        "risk": _risk_counts(prepared.rows),
        "note": "School wellbeing is aggregated. Individual mood notes, journals, and conversations are not included.",
    }


def comparative_payload(actor, prepared: Prepared) -> dict[str, Any]:
    overview = overview_payload(actor, prepared)
    return {
        "available": False,
        "reason": BENCHMARK_REASON,
        "school": {
            "id": actor.school_key,
            "academic_index": overview["academic_health"]["index"],
            "mental_index": overview["school_health"]["mental"]["value"],
            "physical_index": overview["school_health"]["physical"]["value"],
            "student_count": overview["counts"]["students"],
        },
        "benchmark": None,
    }


def assistant_facts(actor, prepared: Prepared) -> dict[str, Any]:
    """Aggregates only. No student names, notes, or journal text."""
    overview = overview_payload(actor, prepared)
    return {
        "school_id": actor.school_key,
        "school_name": actor.school_name,
        "student_count": overview["counts"]["students"],
        "teacher_count": overview["counts"]["teachers"],
        "academic_index": overview["academic_health"]["index"],
        "academic_trend": overview["school_growth"]["academics"]["trend"],
        "mental_index": overview["school_health"]["mental"]["value"],
        "physical_index": overview["school_health"]["physical"]["value"],
        "risk": overview["risk"],
        "subjects": [
            {
                "subject": item["subject"],
                "average": item["average"],
                "trend": item["trend"],
            }
            for item in _subject_table(prepared.rows)
        ],
        "excluded": [
            "student names",
            "journal entries",
            "mood notes",
            "conversations",
            "parent contact details",
        ],
    }

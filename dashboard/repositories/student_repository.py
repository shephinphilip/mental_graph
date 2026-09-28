"""Single-student reads. Still school-checked by the caller."""

from __future__ import annotations

from dashboard.constants import MARKS, MAX_MARKS, MAX_SIGNALS, MEDITATIONS, MOODS, PATTERNS, SLEEP
from dashboard.repositories.dashboard_repository import _find


async def load_student_signals(db, student_id: str) -> dict:
    marks = await _find(
        db[MARKS],
        {"student_id": student_id},
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
    moods = await _find(
        db[MOODS],
        {"user_id": student_id},
        {"user_id": 1, "score": 1, "mood": 1, "logged_at": 1, "created_at": 1, "_id": 0},
        100,
        ("logged_at", -1),
    )
    sleep = await _find(
        db[SLEEP],
        {"user_id": student_id},
        {"user_id": 1, "total_duration_minutes": 1, "date": 1, "created_at": 1, "_id": 0},
        60,
        ("created_at", -1),
    )
    patterns = await _find(
        db[PATTERNS],
        {"user_id": student_id},
        {
            "user_id": 1,
            "pattern_type": 1,
            "domains": 1,
            "status": 1,
            "confidence": 1,
            "last_observed_at": 1,
            "_id": 0,
        },
        20,
        ("last_observed_at", -1),
    )
    turns = await _find(
        db["user_risk_turns"],
        {"user_id": student_id},
        {
            "user_id": 1,
            "risk_intensity_score": 1,
            "crisis_keywords": 1,
            "created_at": 1,
            "band": 1,
            "_id": 0,
        },
        1,
        ("created_at", -1),
    )
    meditations = await _find(
        db[MEDITATIONS],
        {"user_id": student_id},
        {"user_id": 1, "status": 1, "completed_at": 1, "meditation_id": 1, "_id": 0},
        20,
        ("completed_at", -1),
    )
    return {
        "marks": marks,
        "moods": moods,
        "sleep": sleep,
        "patterns": patterns,
        "risk_turns": turns,
        "meditations": meditations,
    }

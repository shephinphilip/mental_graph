"""Single-student academic reads. Psychological series stay off this payload."""

from __future__ import annotations

from dashboard.constants import MARKS, MAX_MARKS
from dashboard.repositories.dashboard_repository import _find


async def load_student_signals(db, student_id: str) -> dict:
    """Marks only. Mood, sleep, patterns, risk, journals, and graphs are not loaded."""
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
    return {"marks": marks}

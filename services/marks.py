"""
services/marks.py — Read-only academic performance records.

Written by the school/admin portal. This service never writes marks.
Recent results are injected into the prompt only when the turn looks academic.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

_ACADEMIC_HINT_RE = re.compile(
    r"\b("
    r"exam|exams|marks?|marksheet|percentage|percentile|rank|jee|neet|clat|"
    r"board|unit test|mock|physics|chemistry|maths?|biology|english|"
    r"fail(?:ed|ing)?|score|syllabus|coaching|kota|paper"
    r")\b",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|namaste|good (morning|evening|afternoon)|thanks|thank you|ok|okay)\s*[!.]*\s*$",
    re.IGNORECASE,
)


def classification_hint(user_message: str) -> str:
    """Coarse turn class used to decide whether marks context is fetched."""
    text = (user_message or "").strip()
    if not text or text.startswith("[Session open]"):
        return "SAFE"
    if _GREETING_RE.match(text):
        return "SAFE"
    if _ACADEMIC_HINT_RE.search(text):
        if re.search(r"\b(marks?|rank|percentage|fail|score)\b", text, re.IGNORECASE):
            return "MARKS"
        if re.search(r"\b(exam|jee|neet|board|mock|paper)\b", text, re.IGNORECASE):
            return "EXAM_STRESS"
        return "NEGATIVE"
    return "SAFE"


async def get_student_marks(
    db: AsyncIOMotorDatabase, student_id: str, limit: int = 20
) -> List[Dict[str, Any]]:
    cursor = (
        db["marks"]
        .find({"student_id": student_id})
        .sort("exam_date", 1)
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def get_recent_marks(
    db: AsyncIOMotorDatabase, student_id: str, limit: int = 5
) -> List[Dict[str, Any]]:
    cursor = (
        db["marks"]
        .find({"student_id": student_id})
        .sort("exam_date", -1)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    docs.reverse()
    return docs


def _fmt_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    return "unknown date"


def build_marks_context(marks: List[Dict[str, Any]]) -> str:
    """Token-efficient prompt block with a one-line trend, not a raw dump."""
    if not marks:
        return "No academic data available"

    lines = []
    percents: List[float] = []
    for doc in marks:
        pct = doc.get("percentage")
        if pct is None and doc.get("total_marks"):
            try:
                pct = round(100.0 * float(doc["marks"]) / float(doc["total_marks"]), 1)
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        if isinstance(pct, (int, float)):
            percents.append(float(pct))
        rank_bit = f", rank {doc['rank']}" if doc.get("rank") is not None else ""
        lines.append(
            f"- {_fmt_date(doc.get('exam_date'))}: {doc.get('subject')} "
            f"({doc.get('exam_type')}) {doc.get('marks')}/{doc.get('total_marks')} "
            f"({pct if pct is not None else '—'}%){rank_bit}"
        )

    trend = "Not enough points to call a trend."
    if len(percents) >= 3:
        delta = percents[-1] - percents[0]
        if delta <= -8:
            trend = f"Declining overall ({percents[0]:.0f}% → {percents[-1]:.0f}%)."
        elif delta >= 8:
            trend = f"Improving overall ({percents[0]:.0f}% → {percents[-1]:.0f}%)."
        else:
            trend = f"Unstable / mixed ({percents[0]:.0f}% → {percents[-1]:.0f}%)."
        latest = percents[-1] - percents[-2]
        if latest <= -10:
            trend += " Latest paper is a sharp drop."
        elif latest >= 10:
            trend += " Latest paper is a sharp rise."

    return (
        "Use only if the student brings up school/exams. Do not recite numbers unprompted.\n"
        f"Trend: {trend}\n"
        + "\n".join(lines)
    )


async def academic_context_for_turn(
    db: AsyncIOMotorDatabase,
    student_id: str,
    user_message: str,
    *,
    opening_turn: bool = False,
) -> Optional[str]:
    hint = classification_hint(user_message)
    if opening_turn or hint == "SAFE":
        return None
    docs = await get_recent_marks(db, student_id)
    return build_marks_context(docs)

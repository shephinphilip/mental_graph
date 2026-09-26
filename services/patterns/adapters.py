"""Domain adapters — read-only views over existing collections.

Missing domains return empty observation lists so detectors degrade gracefully.
Journal rows come from the journaling service, not a second store.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from services.apm import personalization_enabled
from services.marks import get_recent_marks


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


async def collect_observations(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    lookback_days: Optional[int] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Normalize multi-domain observations keyed by domain name.

    Never writes. Never crosses user_id. Omits raw journal/message bodies.
    """
    settings = get_settings()
    days = lookback_days if lookback_days is not None else settings.PATTERN_LOOKBACK_DAYS
    since = datetime.now(timezone.utc) - timedelta(days=days)

    return {
        "mood": await _mood_observations(db, user_id, since),
        "academic": await _academic_observations(db, user_id),
        "habits": await _habit_observations(db, user_id),
        "conversation": await _insight_observations(db, user_id, since),
        "language": await _language_observations(db, user_id),
        "meditation": await _meditation_observations(db, user_id),
        "apm": await _apm_observations(db, user_id),
        "sleep": await _sleep_observations(db, user_id, days),
        "sleep_practices": await _sleep_practice_feedback(db, user_id),
        "journaling": await _journal_observations(db, user_id),
        "meditation_feedback": await _helpful_meditation_days(db, user_id),
        "tasks": await _task_observations(db, user_id, since),
        "attendance": await _attendance_observations(db, user_id),
    }


async def _mood_observations(
    db: AsyncIOMotorDatabase, user_id: str, since: datetime
) -> List[Dict[str, Any]]:
    cursor = (
        db["mood_logs"]
        .find(
            {
                "user_id": user_id,
                "created_at": {"$gte": since},
                # A note carrying crisis wording is stored and surfaced, but it
                # never becomes training signal for pattern detection.
                "crisis_flagged": {"$ne": True},
            }
        )
        .sort("created_at", -1)
        .limit(90)
    )
    docs = await cursor.to_list(length=90)
    out = []
    for doc in docs:
        mood = str(doc.get("mood") or doc.get("label") or "").strip().lower()
        if not mood:
            continue
        score = doc.get("score")
        try:
            score_f = float(score) if score is not None else None
        except (TypeError, ValueError):
            score_f = None
        out.append(
            {
                "feature": "mood_label",
                "value": mood,
                "score": score_f,
                "at": _as_dt(doc.get("created_at")),
                "source": "mood_logs",
                "stressed": mood in {"stressed", "anxious", "overwhelmed", "sad", "low"},
            }
        )
    return out


async def _academic_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    marks = await get_recent_marks(db, user_id, limit=12)
    out = []
    for row in marks:
        pct = row.get("percentage")
        if pct is None and row.get("marks") is not None and row.get("total_marks"):
            try:
                pct = 100.0 * float(row["marks"]) / float(row["total_marks"])
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None
        out.append(
            {
                "feature": "mark_percentage",
                "value": pct,
                "subject": row.get("subject"),
                "at": _as_dt(row.get("exam_date")),
                "source": "marks",
            }
        )
    return out


async def _habit_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    cursor = db["habit_events"].find({"user_id": user_id}).limit(40)
    docs = await cursor.to_list(length=40)
    out = []
    for doc in docs:
        title = str(doc.get("title") or "").lower()
        domain = "habits"
        if "journal" in title:
            domain = "journaling"
        if any(k in title for k in ("meditat", "breath", "body scan", "ground")):
            domain = "meditation"
        out.append(
            {
                "feature": "habit_status",
                "value": doc.get("status"),
                "title": doc.get("title"),
                "streak": doc.get("streak"),
                "domain": domain,
                "source": "habit_events",
                "at": _as_dt(doc.get("updated_at") or doc.get("created_at")),
            }
        )
    return out


async def _insight_observations(
    db: AsyncIOMotorDatabase, user_id: str, since: datetime
) -> List[Dict[str, Any]]:
    cursor = (
        db["user_insights"]
        .find({"user_id": user_id, "created_at": {"$gte": since}})
        .sort("created_at", -1)
        .limit(40)
    )
    docs = await cursor.to_list(length=40)
    out = []
    for doc in docs:
        if doc.get("crisis_signal_detected"):
            # Explicitly exclude crisis turns from pattern evidence
            continue
        themes = doc.get("core_themes") or []
        emotions = doc.get("detected_emotions") or []
        out.append(
            {
                "feature": "insight_themes",
                "value": [str(t).lower() for t in themes],
                "emotions": [str(e).lower() for e in emotions],
                "at": _as_dt(doc.get("created_at")),
                "source": "user_insights",
                "session_id": doc.get("session_id"),
            }
        )
    return out


async def _language_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    doc = await db["users"].find_one(
        {"user_id": user_id}, {"preferred_language": 1, "preferred_language_updated_at": 1}
    )
    if not doc or not doc.get("preferred_language"):
        return []
    return [
        {
            "feature": "preferred_language",
            "value": doc.get("preferred_language"),
            "at": _as_dt(doc.get("preferred_language_updated_at")),
            "source": "users",
        }
    ]


async def _meditation_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    """Derive meditation/coping usage from action_card_logs + graph CopingTool."""
    out: List[Dict[str, Any]] = []
    cursor = (
        db["action_card_logs"]
        .find({"user_id": user_id, "card.card_type": "TOOL_CARD"})
        .sort("created_at", -1)
        .limit(30)
    )
    docs = await cursor.to_list(length=30)
    for doc in docs:
        card = doc.get("card") or {}
        out.append(
            {
                "feature": "tool_suggested",
                "value": card.get("title"),
                "at": _as_dt(doc.get("created_at")),
                "source": "action_card_logs",
            }
        )
    return out


async def _sleep_observations(
    db: AsyncIOMotorDatabase, user_id: str, days: int
) -> List[Dict[str, Any]]:
    """Valid sleep_logs rows only. Raw documents are not copied into patterns."""
    try:
        from sleep.reader import get_sleep_history, stored_duration, valid_records

        docs = valid_records(await get_sleep_history(db, user_id, days=days))
    except Exception:
        return []
    out = []
    for doc in docs:
        minutes = stored_duration(doc)
        if minutes is None:
            continue
        out.append(
            {
                "feature": "sleep_duration_minutes",
                "value": minutes,
                "date": doc.get("date"),
                "bedtime": doc.get("bedtime"),
                "wake_up_time": doc.get("wake_up_time"),
                "observed_at": _as_dt(doc.get("created_at")),
                "source": "sleep_logs",
            }
        )
    return out


async def _task_observations(
    db: AsyncIOMotorDatabase, user_id: str, since: datetime
) -> List[Dict[str, Any]]:
    """Pending items from daily_tasks. Creation and completion are not success."""
    try:
        from tasks.store import recent_task_days

        docs = await recent_task_days(db, user_id, days=14)
    except Exception:
        return []
    out = []
    for doc in docs:
        created = _as_dt(doc.get("created_at"))
        if created is not None and created < since:
            continue
        for task in doc.get("tasks") or []:
            if not isinstance(task, dict) or task.get("is_deleted"):
                continue
            out.append(
                {
                    "feature": "task_pending",
                    "date": str(doc.get("date") or ""),
                    "title": task.get("title"),
                    "pending": 0 if task.get("completed") else 1,
                    "incomplete": not bool(task.get("completed")),
                    "source": "daily_tasks",
                }
            )
    return out


async def _sleep_practice_feedback(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    """Explicit helpfulness on sleep-tagged practices. Not an inferred sleep change."""
    try:
        cursor = (
            db["meditation_executions"]
            .find({"user_id": user_id, "user_helpfulness_feedback": "HELPFUL"})
            .sort("completed_at", -1)
            .limit(40)
        )
        docs = await cursor.to_list(length=40)
    except Exception:
        return []
    out = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        category = str(doc.get("category") or "")
        technique = str(doc.get("technique") or "")
        if category != "sleep" and "SLEEP" not in technique.upper():
            continue
        out.append(
            {
                "user_helpfulness_feedback": "HELPFUL",
                "category": category,
                "technique": technique,
                "source": "meditation_executions",
            }
        )
    return out


async def _journal_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    try:
        from journaling.service import entries_for_patterns

        return await entries_for_patterns(db, user_id)
    except Exception:
        return []


async def _helpful_meditation_days(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    try:
        cursor = (
            db["meditation_executions"]
            .find({"user_id": user_id, "user_helpfulness_feedback": "HELPFUL"})
            .sort("completed_at", -1)
            .limit(40)
        )
        docs = await cursor.to_list(length=40)
    except Exception:
        return []
    out = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        stamp = _as_dt(doc.get("completed_at") or doc.get("started_at"))
        out.append(
            {
                "helpful": True,
                "date": stamp.date().isoformat() if stamp else "",
                "source": "meditation_executions",
            }
        )
    return out


async def _attendance_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    doc = await db["users"].find_one(
        {"user_id": user_id},
        {"attendance_summary": 1, "attendance_data": 1},
    )
    if not doc:
        return []
    summary = doc.get("attendance_summary") or doc.get("attendance_data")
    if not summary:
        return []
    return [
        {
            "feature": "attendance_summary",
            "value": summary if isinstance(summary, (int, float, str)) else "present",
            "source": "users",
            "at": None,
        }
    ]


async def _apm_observations(
    db: AsyncIOMotorDatabase, user_id: str
) -> List[Dict[str, Any]]:
    if not await personalization_enabled(db, user_id):
        return []
    cursor = (
        db["apm_events"]
        .find(
            {
                "user_id": user_id,
                "event_type": {"$in": ["HELPFUL", "NOT_HELPFUL"]},
            }
        )
        .sort("created_at", -1)
        .limit(40)
    )
    docs = await cursor.to_list(length=40)
    out = []
    for doc in docs:
        out.append(
            {
                "feature": "intervention_feedback",
                "value": doc.get("event_type"),
                "intervention_id": doc.get("intervention_id"),
                "edge_id": doc.get("edge_id"),
                "at": _as_dt(doc.get("created_at")),
                "source": "apm_events",
                "helpful": doc.get("event_type") == "HELPFUL",
            }
        )
    return out


def personal_baseline(values: List[float], window: int = 30) -> Optional[float]:
    sample = [v for v in values[:window] if v is not None]
    if len(sample) < 3:
        return None
    return sum(sample) / len(sample)


def group_marks_by_subject(
    academic: List[Dict[str, Any]]
) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in academic:
        subject = str(row.get("subject") or "unknown")
        if row.get("value") is None:
            continue
        grouped[subject].append(row)
    for subject in grouped:
        grouped[subject].sort(key=lambda r: r.get("at") or datetime.min.replace(tzinfo=timezone.utc))
    return grouped

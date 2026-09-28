"""
Derived longitudinal student profile.

Source collections stay the source of truth. This module writes one
``student_psychological_profiles`` document per user and reads that single
document back for chat. It does not copy transcripts, invent marks, or
turn a pattern into a diagnosis.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from config.config import logger
from services.apm import personalization_enabled

COLLECTION = "student_psychological_profiles"
PROFILE_VERSION = 1
MIN_EVIDENCE = 3
RECENT_SESSIONS = 10
TRENDS = {"increasing", "decreasing", "stable", "insufficient_data"}
SCORE_DOMAINS = (
    "stress",
    "emotional_distress",
    "academic_pressure",
    "social_difficulty",
    "coping_difficulty",
    "sleep_disruption",
    "overall_distress",
)
STRESS_MOODS = {"stressed", "anxious", "overwhelmed", "sad", "low"}
DISTRESS_MOODS = {"sad", "low", "hopeless", "empty", "numb"}
EXAM_TOPICS = {"exam", "exams", "marks", "mark", "study", "pressure", "test"}
SOCIAL_TOPICS = {"friend", "friends", "lonely", "loneliness", "bully", "bullying", "left_out"}

EMPTY_CONTEXT = "No consolidated student profile yet."
_LOCKS: Dict[str, asyncio.Lock] = {}


def _now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    return current if current.tzinfo else current.replace(tzinfo=timezone.utc)


def _pget(profile: Dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = profile
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return default if current is None else current


def _join(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value if item not in (None, ""))
    return str(value)


def _blank_score() -> Dict[str, Any]:
    return {
        "score": None,
        "confidence": None,
        "evidence_count": 0,
        "trend": "insufficient_data",
        "last_updated": None,
    }


def _score_block(
    score: Optional[int],
    evidence: int,
    trend: str,
    now: datetime,
) -> Dict[str, Any]:
    if score is None or evidence < MIN_EVIDENCE:
        block = _blank_score()
        block["evidence_count"] = int(evidence)
        return block
    bounded = max(1, min(10, int(score)))
    return {
        "score": bounded,
        "confidence": round(min(1.0, evidence / 8.0), 2),
        "evidence_count": int(evidence),
        "trend": trend if trend in TRENDS else "insufficient_data",
        "last_updated": now,
    }


def trend_of(values: Sequence[float], *, invert: bool = False) -> str:
    """Compare the older half of a chronological series with the newer half."""
    series = [float(item) for item in values]
    if len(series) < 4:
        return "insufficient_data"
    mid = len(series) // 2
    older = sum(series[:mid]) / mid
    newer = sum(series[mid:]) / (len(series) - mid)
    delta = older - newer if invert else newer - older
    if abs(delta) < 0.5:
        return "stable"
    return "increasing" if delta > 0 else "decreasing"


def _hours(minutes: Any) -> Optional[float]:
    try:
        value = float(minutes) / 60.0
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > 18:
        return None
    return round(value, 2)


def _sleep_disruption_score(average_hours: float) -> int:
    if average_hours >= 8:
        return 2
    if average_hours >= 7:
        return 4
    if average_hours >= 6:
        return 6
    if average_hours >= 5:
        return 8
    return 9


def _mean(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _chrono(rows: Iterable[Dict[str, Any]], *keys: str) -> List[Dict[str, Any]]:
    def stamp(row: Dict[str, Any]) -> str:
        for key in keys:
            value = row.get(key)
            if value is not None:
                return str(value)
        return ""

    return sorted(rows, key=stamp)


def _explicit_bool(user: Dict[str, Any], key: str) -> bool:
    return user.get(key) is True


def _topics(values: Iterable[Any]) -> List[str]:
    found: List[str] = []
    for value in values:
        if isinstance(value, (list, tuple)):
            found.extend(_topics(value))
        elif value not in (None, ""):
            found.append(str(value).strip().lower())
    return found


def _recurring(labels: Sequence[str]) -> List[str]:
    counts: Dict[str, int] = {}
    for label in labels:
        if not label:
            continue
        counts[label] = counts.get(label, 0) + 1
    return [label for label, count in counts.items() if count >= 2]


def assemble_profile(
    user: Dict[str, Any],
    observations: Dict[str, List[Dict[str, Any]]],
    reports: Sequence[Dict[str, Any]],
    executions: Sequence[Dict[str, Any]],
    *,
    personalization: bool,
    now: Optional[datetime] = None,
    existing_created_at: Any = None,
) -> Dict[str, Any]:
    """Build one profile dict from already loaded, user-scoped rows. No I/O."""
    stamp = _now(now)
    user_id = str(user.get("user_id") or "")
    moods = observations.get("mood") or []
    academic = [row for row in (observations.get("academic") or []) if row.get("value") is not None]
    habits = observations.get("habits") or []
    insights = observations.get("conversation") or []
    sleep_rows = observations.get("sleep") or []
    journal_rows = observations.get("journaling") or []
    tasks = observations.get("tasks") or []
    attendance_rows = observations.get("attendance") or []

    identity = {
        "name": user.get("name") or "",
        "age": user.get("age"),
        "current_class": user.get("class") or user.get("grade") or user.get("class_level") or "",
        "city": user.get("city") or "",
        "school": user.get("school") or "",
        "board": user.get("board") or user.get("school_board") or "",
        "preferred_language": user.get("preferred_language") or "ENGLISH",
        "communication_style": user.get("communication_style") or "",
        "known_preferences": list(user.get("known_preferences") or []) if personalization else [],
    }

    prior = user.get("prior_diagnoses") if isinstance(user.get("prior_diagnoses"), list) else []
    latest_report = reports[0] if reports else {}
    if latest_report.get("crisis_signal") is True:
        stored_risk = "historical_crisis_signal_in_latest_stored_report"
    elif reports:
        stored_risk = "no_crisis_flag_on_stored_reports"
    else:
        stored_risk = ""

    mental = {
        "chief_concern": user.get("chief_concern") or "",
        "prior_diagnoses": [str(item) for item in prior if item],
        "current_risk_level": stored_risk,
        "suicidal_ideation": _explicit_bool(user, "suicidal_ideation"),
        "suicidal_intent": _explicit_bool(user, "suicidal_intent"),
        "self_harm_history": _explicit_bool(user, "self_harm_history"),
        "self_harm_recent": _explicit_bool(user, "self_harm_recent"),
        "protective_factors": list(user.get("protective_factors") or [])
        if isinstance(user.get("protective_factors"), list)
        else [],
        "stress_pattern": "",
        "anxiety_pattern": "",
        "social_pattern": "",
        "coping_pattern": "",
        "trigger_patterns": [],
        "psychological_patterns": "",
    }

    stress_flags = [1.0 if row.get("stressed") else 0.0 for row in _chrono(moods, "at", "created_at")]
    stressed = int(sum(stress_flags))
    stress_trend = trend_of(stress_flags)
    stress_score = round(10 * stressed / len(stress_flags)) if stress_flags else None
    distress_flags = [
        1.0 if str(row.get("value") or "") in DISTRESS_MOODS else 0.0
        for row in _chrono(moods, "at", "created_at")
    ]
    distress_count = int(sum(distress_flags))
    distress_score = round(10 * distress_count / len(distress_flags)) if distress_flags else None

    percentages = [float(row["value"]) for row in _chrono(academic, "at", "exam_date")]
    exam_hits = 0
    social_hits = 0
    theme_labels: List[str] = []
    for row in insights:
        labels = _topics(row.get("value") or [])
        theme_labels.extend(labels)
        exam_hits += sum(1 for label in labels if label in EXAM_TOPICS)
        social_hits += sum(1 for label in labels if label in SOCIAL_TOPICS)
    for row in journal_rows:
        labels = _topics(row.get("topics") or [])
        theme_labels.extend(labels)
        exam_hits += sum(1 for label in labels if label in EXAM_TOPICS)
        social_hits += sum(1 for label in labels if label in SOCIAL_TOPICS)

    academic_evidence = len(percentages) + exam_hits
    academic_score = None
    academic_trend = trend_of(percentages, invert=True)
    if len(percentages) >= MIN_EVIDENCE:
        latest = percentages[-1]
        academic_score = 8 if latest < 50 else 6 if latest < 70 else 3
        if academic_trend == "increasing":
            academic_score = min(10, academic_score + 1)
    elif exam_hits >= MIN_EVIDENCE:
        academic_score = min(10, 4 + exam_hits)
        academic_trend = "insufficient_data"

    social_score = min(10, social_hits) if social_hits >= MIN_EVIDENCE else None
    unhelpful = [
        row for row in executions if str(row.get("user_helpfulness_feedback") or "").upper() == "NOT_HELPFUL"
    ]
    helpful = [
        row for row in executions if str(row.get("user_helpfulness_feedback") or "").upper() == "HELPFUL"
    ]
    coping_score = min(10, 4 + len(unhelpful)) if len(unhelpful) >= MIN_EVIDENCE else None

    sleep_hours = []
    for row in _chrono(sleep_rows, "date", "observed_at"):
        hours = _hours(row.get("value"))
        if hours is not None:
            sleep_hours.append(hours)
    average_sleep = _mean(sleep_hours)
    sleep_trend = trend_of(sleep_hours, invert=True)
    sleep_score = (
        _sleep_disruption_score(average_sleep)
        if average_sleep is not None and len(sleep_hours) >= MIN_EVIDENCE
        else None
    )

    domain_blocks = {
        "stress": _score_block(stress_score, len(stress_flags), stress_trend, stamp),
        "emotional_distress": _score_block(
            distress_score, len(distress_flags), trend_of(distress_flags), stamp
        ),
        "academic_pressure": _score_block(academic_score, academic_evidence, academic_trend, stamp),
        "social_difficulty": _score_block(social_score, social_hits, "insufficient_data", stamp),
        "coping_difficulty": _score_block(coping_score, len(unhelpful), "insufficient_data", stamp),
        "sleep_disruption": _score_block(sleep_score, len(sleep_hours), sleep_trend, stamp),
    }
    scored = [block for block in domain_blocks.values() if block["score"] is not None]
    if len(scored) >= 2:
        overall = round(sum(block["score"] for block in scored) / len(scored))
        overall_evidence = sum(block["evidence_count"] for block in scored)
        domain_blocks["overall_distress"] = _score_block(overall, overall_evidence, "insufficient_data", stamp)
    else:
        domain_blocks["overall_distress"] = _blank_score()

    if not personalization:
        domain_blocks = {name: _blank_score() for name in SCORE_DOMAINS}

    if personalization and domain_blocks["stress"]["score"] is not None:
        mental["stress_pattern"] = (
            f"Selected mood check-ins marked stressed, anxious, overwhelmed, sad, or low "
            f"in {stressed} of {len(stress_flags)} recent logs."
        )
    if personalization and social_hits >= MIN_EVIDENCE:
        mental["social_pattern"] = (
            "Friend, loneliness, or bullying themes appear more than once in stored "
            "journal topics or insight tags."
        )
    recurring_themes = _recurring(theme_labels) if personalization else []
    mental["trigger_patterns"] = recurring_themes[:8] if personalization else []
    if personalization and mental["stress_pattern"]:
        mental["psychological_patterns"] = (
            "Patterns below are counts from stored logs. They are not diagnoses."
        )

    last_week = sleep_hours[-7:] if sleep_hours else []
    sleep_block = {
        "sleep_hours_last_week": round(sum(last_week) / len(last_week), 2) if last_week else None,
        "sleep_quality": "",
        "sleep_pattern": "",
        "average_sleep_hours": round(average_sleep, 2) if average_sleep is not None else None,
        "recent_sleep_change": sleep_trend if len(sleep_hours) >= 4 else "insufficient_data",
        "sleep_evidence_count": len(sleep_hours),
    }
    if len(sleep_hours) >= MIN_EVIDENCE and average_sleep is not None:
        sleep_block["sleep_pattern"] = (
            f"Average of {len(sleep_hours)} stored nights is {average_sleep:.1f} hours. "
            "This describes the logs. It does not explain why."
        )

    mood_labels = [str(row.get("mood")) for row in journal_rows if row.get("mood")]
    journal_block = {
        "journal_summary": "",
        "journal_mood_pattern": "",
        "recurring_journal_themes": recurring_themes[:8] if personalization else [],
        "recent_journal_entries_summary": "",
        "journal_entry_count_recent": len(journal_rows),
        "journal_trend": "insufficient_data",
        "last_journal_at": journal_rows[0].get("observed_at") or journal_rows[0].get("date") if journal_rows else None,
    }
    if personalization and journal_rows:
        journal_block["journal_summary"] = (
            "Recent journal entries are summarized from selected moods and topic tags, not a diagnosis."
        )
        if mood_labels:
            journal_block["journal_mood_pattern"] = (
                "Manually selected moods: " + ", ".join(mood_labels[:8])
            )
        if recurring_themes:
            journal_block["recent_journal_entries_summary"] = (
                "Recurring topic tags include " + ", ".join(recurring_themes[:5]) + "."
            )

    by_subject: Dict[str, List[float]] = {}
    for row in _chrono(academic, "at", "exam_date"):
        subject = str(row.get("subject") or "").strip()
        if subject:
            by_subject.setdefault(subject, []).append(float(row["value"]))
    strengths = [name for name, vals in by_subject.items() if vals[-1] >= 75]
    struggles = [name for name, vals in by_subject.items() if vals[-1] < 45]
    academics = {
        "recent_marks_percentage": round(percentages[-1], 2) if percentages else None,
        "marks_trend": trend_of(percentages) if len(percentages) >= 4 else "insufficient_data",
        "subject_performance": {
            name: round(vals[-1], 2) for name, vals in by_subject.items()
        },
        "academic_strengths": strengths,
        "academic_struggles": struggles,
        "family_academic_pressure_level": "",
        "academic_pattern_summary": "",
        "last_marks_update": academic[-1].get("at") if academic else None,
    }
    if percentages:
        academics["academic_pattern_summary"] = (
            f"Latest stored percentage is {academics['recent_marks_percentage']}. "
            f"Trend across stored assessments: {academics['marks_trend']}."
        )

    attendance_value = attendance_rows[0].get("value") if attendance_rows else None
    attendance_pct = attendance_value if isinstance(attendance_value, (int, float)) else None
    absence_reason = ""
    if isinstance(attendance_value, dict):
        raw_pct = attendance_value.get("percentage", attendance_value.get("attendance_percentage"))
        attendance_pct = float(raw_pct) if isinstance(raw_pct, (int, float)) else None
        absence_reason = str(attendance_value.get("recent_absence_reason") or "")
    attendance = {
        "attendance_percentage": attendance_pct,
        "attendance_trend": "insufficient_data",
        "attendance_pattern": "",
        "recent_absence_reason": absence_reason,
        "attendance_pattern_summary": "",
        "last_attendance_update": None,
    }
    if attendance_pct is not None:
        attendance["attendance_pattern_summary"] = (
            f"Stored attendance percentage is {attendance_pct}. No absence series is on file."
        )

    completed = [row for row in executions if str(row.get("status") or "").upper() == "COMPLETED"]
    practice_ids = []
    for row in completed:
        practice = row.get("meditation_id")
        if practice and practice not in practice_ids:
            practice_ids.append(str(practice))
    recent_completed = [
        {
            "meditation_id": str(row.get("meditation_id") or ""),
            "completed_at": row.get("completed_at"),
        }
        for row in completed[:5]
        if row.get("meditation_id")
    ]
    meditation = {
        "classes_or_practices_taken": practice_ids,
        "meditation_practices_used": practice_ids,
        "meditation_response_pattern": "",
        "helpful_meditation_interventions": [str(row.get("meditation_id")) for row in helpful if row.get("meditation_id")]
        if personalization
        else [],
        "unhelpful_meditation_interventions": [
            str(row.get("meditation_id")) for row in unhelpful if row.get("meditation_id")
        ]
        if personalization
        else [],
        "total_completed_sessions": len(completed),
        "recent_completed_sessions": recent_completed,
        "last_meditation_at": completed[0].get("completed_at") if completed else None,
    }
    if personalization and (helpful or unhelpful):
        meditation["meditation_response_pattern"] = (
            f"{len(completed)} completed practices. "
            f"{len(helpful)} explicitly marked helpful and {len(unhelpful)} explicitly marked not helpful. "
            "Starting or opening a practice is not treated as helpful."
        )

    interventions = {
        "helpful_interventions": meditation["helpful_meditation_interventions"],
        "unhelpful_interventions": meditation["unhelpful_meditation_interventions"],
        "recovery_patterns": [],
        "preferred_coping_methods": [],
    }

    recent = list(reports)[:RECENT_SESSIONS]
    snapshots = []
    unresolved: List[str] = []
    resolved: List[str] = []
    topic_labels: List[str] = []
    for report in recent:
        events = [event for event in (report.get("events") or []) if isinstance(event, dict)]
        labels = [str(event.get("label")) for event in events if event.get("label")]
        topic_labels.extend(labels)
        for event in events:
            label = str(event.get("label") or "")
            if not label:
                continue
            if event.get("resolved") is True:
                resolved.append(label)
            elif event.get("resolved") is False:
                unresolved.append(label)
        concern = str(report.get("psychiatric_summary") or report.get("summary") or "").strip()
        snapshots.append(
            {
                "session_id": str(report.get("session_id") or ""),
                "date": report.get("created_at"),
                "main_concern": concern[:180],
                "emotional_state": "",
                "important_topics": labels[:6],
                "actions_discussed": [
                    str(task.get("title"))
                    for task in (report.get("proposed_tasks") or report.get("tasks") or [])
                    if isinstance(task, dict) and task.get("title")
                ][:4],
                "outcome": "",
                "risk_signal": "crisis_flag" if report.get("crisis_signal") else "",
            }
        )
    recurring_topics = _recurring(topic_labels)
    conversations = {
        "session_count": len(reports) if len(reports) < RECENT_SESSIONS else max(len(reports), RECENT_SESSIONS),
        "recent_sessions": snapshots if personalization else [],
        "recent_session_summary": snapshots[0]["main_concern"] if personalization and snapshots else "",
        "longitudinal_conversation_summary": "",
        "recurring_conversation_topics": recurring_topics if personalization else [],
        "recent_emotional_changes": "",
        "previously_discussed_issues": list(dict.fromkeys(topic_labels))[:8] if personalization else [],
        "resolved_issues": list(dict.fromkeys(resolved)) if personalization else [],
        "unresolved_issues": list(dict.fromkeys(unresolved)) if personalization else [],
        "last_session_at": recent[0].get("created_at") if recent else None,
    }
    if personalization and recurring_topics:
        conversations["longitudinal_conversation_summary"] = (
            "Recurring topics across stored session readings: " + ", ".join(recurring_topics[:6]) + "."
        )

    pending = [str(row.get("title")) for row in tasks if row.get("incomplete") and row.get("title")]
    finished = [row for row in tasks if row.get("pending") == 0]
    active_habits = [
        str(row.get("title"))
        for row in habits
        if str(row.get("value") or row.get("status") or "").lower() == "active" and row.get("title")
    ]
    tasks_block = {
        "active_tasks": pending[:8],
        "task_completion_pattern": "",
        "habit_patterns": "",
        "helpful_habits": [],
        "struggling_habits": [
            str(row.get("title"))
            for row in habits
            if str(row.get("value") or "").lower() in {"struggling", "paused"} and row.get("title")
        ],
    }
    if tasks:
        tasks_block["task_completion_pattern"] = (
            f"{len(finished)} of {len(tasks)} recorded task rows are complete. "
            "Completion is not evidence of psychological recovery."
        )
    if active_habits:
        tasks_block["habit_patterns"] = "Active habits: " + ", ".join(active_habits[:6]) + "."

    filled = 0
    if identity["name"] or identity["age"] is not None:
        filled += 1
    if sleep_block["sleep_evidence_count"]:
        filled += 1
    if journal_block["journal_entry_count_recent"]:
        filled += 1
    if academics["recent_marks_percentage"] is not None:
        filled += 1
    if attendance["attendance_percentage"] is not None:
        filled += 1
    if meditation["total_completed_sessions"]:
        filled += 1
    if conversations["session_count"]:
        filled += 1
    if tasks_block["active_tasks"] or tasks_block["habit_patterns"]:
        filled += 1
    if any(domain_blocks[name]["score"] is not None for name in SCORE_DOMAINS):
        filled += 1
    if mental["chief_concern"]:
        filled += 1

    return {
        "user_id": user_id,
        "profile_version": PROFILE_VERSION,
        "identity": identity,
        "mental_health": mental,
        "psychological_pattern_scores": domain_blocks,
        "sleep": sleep_block,
        "journaling": journal_block,
        "academics": academics,
        "attendance": attendance,
        "meditation": meditation,
        "interventions": interventions,
        "conversations": conversations,
        "tasks_and_habits": tasks_block,
        "metadata": {
            "first_profile_created_at": existing_created_at or stamp,
            "last_profile_updated_at": stamp,
            "last_consolidated_at": stamp,
            "source_counts": {
                "mood": len(moods),
                "marks": len(percentages),
                "sleep": len(sleep_hours),
                "journal": len(journal_rows),
                "sessions_loaded": len(recent),
                "meditation_executions": len(list(executions)),
                "tasks": len(tasks),
                "attendance": 1 if attendance_rows else 0,
            },
            "data_completeness": round(filled / 10.0, 2),
            "personalization_applied": personalization,
        },
    }


def format_profile_context(profile: Dict[str, Any], *, personalization: bool) -> str:
    """Compact prompt block. Empty fields are omitted. Scores are labeled as estimates."""
    lines = [
        "STUDENT PROFILE",
        "These fields are longitudinal observations and derived patterns, not a diagnosis.",
        "Use at most one of them, and only if it fits the current message.",
        "Do not quote scores, confidence, profile fields, or say you checked a profile.",
        "Say a connection in ordinary language, or say nothing from this block.",
        "The current message and the live safety check override this profile.",
        "A stored low-risk note must not suppress a current crisis signal.",
        "An old high score is not a current crisis without a current signal.",
    ]
    if not personalization:
        lines.append(
            "Personalization consent is off. Derived scores, journal themes, "
            "intervention learning, and conversation summaries are not used."
        )
        language = _pget(profile, "identity.preferred_language", "ENGLISH")
        lines.append(f"Preferred language: {language}")
        return "\n".join(lines)

    field_map = {
        "name": f"Name: {_pget(profile, 'identity.name', '')}",
        "age": f"Age: {_pget(profile, 'identity.age', '')}",
        "current_class": f"Class: {_pget(profile, 'identity.current_class', '')}",
        "city": f"City: {_pget(profile, 'identity.city', '')}",
        "school": f"School: {_pget(profile, 'identity.school', '')}",
        "board": f"Board: {_pget(profile, 'identity.board', '')}",
        "preferred_language": (
            f"Preferred language: {_pget(profile, 'identity.preferred_language', 'ENGLISH')}"
        ),
        "communication_style": (
            f"Communication style: {_pget(profile, 'identity.communication_style', '')}"
        ),
        "known_preferences": (
            f"Known preferences: {_join(_pget(profile, 'identity.known_preferences'))}"
        ),
        "chief_concern": f"Chief concern: {_pget(profile, 'mental_health.chief_concern', '')}",
        "prior_diagnoses": f"Diagnoses on file: {_join(_pget(profile, 'mental_health.prior_diagnoses'))}",
        "current_risk_level": (
            f"Stored risk observation (not the live safety decision): "
            f"{_pget(profile, 'mental_health.current_risk_level', '')}"
        ),
        "protective_factors": (
            f"Protective factors: {_join(_pget(profile, 'mental_health.protective_factors'))}"
        ),
        "psychological_patterns": (
            f"Psychological patterns: {_pget(profile, 'mental_health.psychological_patterns', '')}"
        ),
        "stress_pattern": f"Stress pattern: {_pget(profile, 'mental_health.stress_pattern', '')}",
        "social_pattern": f"Social/relationship pattern: {_pget(profile, 'mental_health.social_pattern', '')}",
        "trigger_patterns": (
            f"Known triggers/patterns: {_join(_pget(profile, 'mental_health.trigger_patterns'))}"
        ),
        "sleep_hours_last_week": (
            f"Sleep: {_pget(profile, 'sleep.sleep_hours_last_week', '')} hrs/night"
        ),
        "sleep_pattern": f"Sleep pattern: {_pget(profile, 'sleep.sleep_pattern', '')}",
        "recent_sleep_change": f"Recent sleep change: {_pget(profile, 'sleep.recent_sleep_change', '')}",
        "journal_summary": f"Recent journal themes: {_pget(profile, 'journaling.journal_summary', '')}",
        "journal_mood_pattern": (
            f"Journal mood pattern: {_pget(profile, 'journaling.journal_mood_pattern', '')}"
        ),
        "recurring_journal_themes": (
            f"Recurring journal themes: {_join(_pget(profile, 'journaling.recurring_journal_themes'))}"
        ),
        "recent_journal_entries": (
            f"Recent journal context: {_pget(profile, 'journaling.recent_journal_entries_summary', '')}"
        ),
        "recent_marks_percentage": (
            f"Recent marks: {_pget(profile, 'academics.recent_marks_percentage', '')}%"
        ),
        "marks_trend": f"Marks trend: {_pget(profile, 'academics.marks_trend', '')}",
        "subject_performance": (
            f"Subject performance: {_pget(profile, 'academics.subject_performance', '')}"
        ),
        "academic_strengths": (
            f"Academic strengths: {_join(_pget(profile, 'academics.academic_strengths'))}"
        ),
        "academic_struggles": (
            f"Academic struggles: {_join(_pget(profile, 'academics.academic_struggles'))}"
        ),
        "academic_pattern_summary": (
            f"Academic pattern: {_pget(profile, 'academics.academic_pattern_summary', '')}"
        ),
        "attendance_percentage": (
            f"Attendance: {_pget(profile, 'attendance.attendance_percentage', '')}%"
        ),
        "attendance_pattern_summary": (
            f"Attendance pattern: {_pget(profile, 'attendance.attendance_pattern_summary', '')}"
        ),
        "recent_absence_reason": (
            f"Recent absence context: {_pget(profile, 'attendance.recent_absence_reason', '')}"
        ),
        "meditation_practices_used": (
            f"Completed practices: {_join(_pget(profile, 'meditation.meditation_practices_used'))}"
        ),
        "meditation_response_pattern": (
            f"Meditation response pattern: {_pget(profile, 'meditation.meditation_response_pattern', '')}"
        ),
        "helpful_interventions": (
            f"Helpful interventions: {_join(_pget(profile, 'interventions.helpful_interventions'))}"
        ),
        "unhelpful_interventions": (
            f"Unhelpful interventions: {_join(_pget(profile, 'interventions.unhelpful_interventions'))}"
        ),
        "session_count": f"Past conversation sessions loaded: {_pget(profile, 'conversations.session_count', 0)}",
        "recent_session_summary": (
            f"Recent session summary: {_pget(profile, 'conversations.recent_session_summary', '')}"
        ),
        "longitudinal_conversation_summary": (
            f"Longitudinal conversation summary: "
            f"{_pget(profile, 'conversations.longitudinal_conversation_summary', '')}"
        ),
        "recurring_conversation_topics": (
            f"Recurring conversation topics: "
            f"{_join(_pget(profile, 'conversations.recurring_conversation_topics'))}"
        ),
        "previously_discussed_issues": (
            f"Previously discussed issues: "
            f"{_join(_pget(profile, 'conversations.previously_discussed_issues'))}"
        ),
        "resolved_issues": f"Resolved issues: {_join(_pget(profile, 'conversations.resolved_issues'))}",
        "unresolved_issues": (
            f"Unresolved issues: {_join(_pget(profile, 'conversations.unresolved_issues'))}"
        ),
        "active_tasks": f"Active tasks: {_join(_pget(profile, 'tasks_and_habits.active_tasks'))}",
        "task_completion_pattern": (
            f"Task completion pattern: {_pget(profile, 'tasks_and_habits.task_completion_pattern', '')}"
        ),
        "habit_patterns": f"Habit patterns: {_pget(profile, 'tasks_and_habits.habit_patterns', '')}",
        "struggling_habits": (
            f"Struggling habits: {_join(_pget(profile, 'tasks_and_habits.struggling_habits'))}"
        ),
    }
    for key, line in field_map.items():
        payload = line.split(": ", 1)[-1].strip()
        if payload in {"", "%", "/10", "hrs/night", "0", "{}"}:
            continue
        if key == "session_count" and payload in {"0", ""}:
            continue
        if key == "current_risk_level" and "no_crisis" in payload:
            continue
        if key == "recent_sleep_change" and payload == "insufficient_data":
            continue
        if key == "marks_trend" and payload == "insufficient_data":
            continue
        lines.append(line)

    for domain in SCORE_DOMAINS:
        block = _pget(profile, f"psychological_pattern_scores.{domain}", {}) or {}
        if not isinstance(block, dict) or block.get("score") is None:
            continue
        lines.append(
            f"{domain.replace('_', ' ').capitalize()}: {block['score']}/10 "
            f"(confidence {block.get('confidence')}, trend {block.get('trend')}, "
            f"evidence {block.get('evidence_count')}). Internal only. "
            "Do not quote this number to the student."
        )
    if _pget(profile, "mental_health.suicidal_ideation") is True:
        lines.append("Stored flag: suicidal ideation is explicitly recorded on the user profile.")
    if _pget(profile, "mental_health.suicidal_intent") is True:
        lines.append("Stored flag: suicidal intent is explicitly recorded on the user profile.")
    if _pget(profile, "mental_health.self_harm_recent") is True:
        lines.append("Stored flag: recent self-harm is explicitly recorded on the user profile.")
    return "\n".join(lines)


def sanitize_profile(doc: Dict[str, Any], *, personalization: bool) -> Dict[str, Any]:
    """API view. Drops Mongo ids and, when consent is off, derived psychological narrative."""
    hidden = {"_id"}
    cleaned = {key: value for key, value in doc.items() if key not in hidden}
    cleaned.pop("metadata", None)
    meta = doc.get("metadata") or {}
    cleaned["metadata"] = {
        "first_profile_created_at": meta.get("first_profile_created_at"),
        "last_profile_updated_at": meta.get("last_profile_updated_at"),
        "last_consolidated_at": meta.get("last_consolidated_at"),
        "data_completeness": meta.get("data_completeness"),
        "source_counts": meta.get("source_counts") or {},
        "personalization_applied": bool(personalization),
    }
    if personalization:
        return cleaned
    redacted = assemble_profile(
        {
            "user_id": doc.get("user_id"),
            "name": _pget(doc, "identity.name", ""),
            "age": _pget(doc, "identity.age"),
            "class": _pget(doc, "identity.current_class", ""),
            "city": _pget(doc, "identity.city", ""),
            "school": _pget(doc, "identity.school", ""),
            "board": _pget(doc, "identity.board", ""),
            "preferred_language": _pget(doc, "identity.preferred_language", "ENGLISH"),
            "chief_concern": _pget(doc, "mental_health.chief_concern", ""),
            "prior_diagnoses": _pget(doc, "mental_health.prior_diagnoses", []),
            "suicidal_ideation": _pget(doc, "mental_health.suicidal_ideation", False),
            "suicidal_intent": _pget(doc, "mental_health.suicidal_intent", False),
            "self_harm_history": _pget(doc, "mental_health.self_harm_history", False),
            "self_harm_recent": _pget(doc, "mental_health.self_harm_recent", False),
            "protective_factors": _pget(doc, "mental_health.protective_factors", []),
        },
        {},
        [],
        [],
        personalization=False,
        existing_created_at=meta.get("first_profile_created_at"),
    )
    redacted["metadata"] = cleaned["metadata"]
    redacted["metadata"]["personalization_applied"] = False
    return redacted


async def _load_reports(db, user_id: str) -> List[Dict[str, Any]]:
    try:
        cursor = db["session_reports"].find({"user_id": user_id})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("created_at", -1)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(RECENT_SESSIONS)
        rows = await cursor.to_list(length=RECENT_SESSIONS)
    except Exception:
        logger.exception("Session report read failed for profile user=%s", user_id)
        return []
    return [row for row in rows if isinstance(row, dict)]


async def _load_executions(db, user_id: str) -> List[Dict[str, Any]]:
    try:
        cursor = db["meditation_executions"].find({"user_id": user_id})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("completed_at", -1)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(40)
        rows = await cursor.to_list(length=40)
    except Exception:
        logger.exception("Meditation execution read failed for profile user=%s", user_id)
        return []
    return [row for row in rows if isinstance(row, dict)]


async def consolidate_student_profile(
    db,
    user_id: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Rebuild the one profile for this user. Does not write source collections."""
    if not user_id:
        raise ValueError("user_id is required")
    lock = _LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        from services.patterns.adapters import collect_observations
        from services.users import get_by_identifier

        user = await get_by_identifier(db, user_id)
        if not user or not user.get("isActive", True):
            raise LookupError("Active user not found")
        personalization = await personalization_enabled(db, user_id)
        observations = await collect_observations(db, user_id)
        reports = await _load_reports(db, user_id)
        executions = await _load_executions(db, user_id)
        existing = await db[COLLECTION].find_one({"user_id": user_id})
        created = None
        if isinstance(existing, dict):
            created = (existing.get("metadata") or {}).get("first_profile_created_at")
        profile = assemble_profile(
            user,
            observations,
            reports,
            executions,
            personalization=personalization,
            now=now,
            existing_created_at=created,
        )
        if reports:
            profile["conversations"]["session_count"] = len(reports)
        session_count = len(reports)
        counter = getattr(db["session_reports"], "count_documents", None)
        if counter is not None:
            try:
                session_count = int(await counter({"user_id": user_id}))
            except Exception:
                session_count = len(reports)
        profile["conversations"]["session_count"] = session_count
        await db[COLLECTION].update_one({"user_id": user_id}, {"$set": profile}, upsert=True)
        return profile


async def get_student_profile(db, user_id: str) -> Optional[Dict[str, Any]]:
    doc = await db[COLLECTION].find_one({"user_id": user_id})
    return doc if isinstance(doc, dict) else None


async def read_profile_for_owner(db, user_id: str) -> Optional[Dict[str, Any]]:
    doc = await get_student_profile(db, user_id)
    if not doc:
        return None
    return sanitize_profile(doc, personalization=await personalization_enabled(db, user_id))


async def get_student_profile_context(db, user_id: str) -> str:
    """One-document read for chat. Never scans source collections."""
    try:
        doc = await get_student_profile(db, user_id)
        if not doc:
            return EMPTY_CONTEXT
        return format_profile_context(
            doc,
            personalization=await personalization_enabled(db, user_id),
        )
    except Exception:
        logger.exception("Student profile context failed user=%s", user_id)
        return EMPTY_CONTEXT


async def delete_student_profile(db, user_id: str) -> int:
    result = await db[COLLECTION].delete_many({"user_id": user_id})
    return int(getattr(result, "deleted_count", 0) or 0)


async def refresh_student_profile_background(db, user_id: str) -> None:
    """Background rebuild. Failures stay off the request path."""
    try:
        await consolidate_student_profile(db, user_id)
    except Exception:
        logger.exception("Background profile refresh failed user=%s", user_id)


def enqueue_profile_refresh(background_tasks, db, user_id: str) -> None:
    background_tasks.add_task(refresh_student_profile_background, db, user_id)


async def ensure_student_profile_indexes(db) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1)],
        unique=True,
        name="uniq_student_psychological_profile_user",
    )
    logger.info("Student psychological profile index ensured")

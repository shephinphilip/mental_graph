"""Dashboard math.

Risk bands reuse stored ``users.current_risk_level`` and ``user_risk_turns``
scored by ``services.risk_assessor`` (crisis / score >= 8 → critical,
score >= 5 → at risk). Patterns only contribute a watch flag. This module
does not rescore message text.

The performance quadrant reuses the mark-trend cutoffs in ``services.marks``
(±8 points) and the strength / struggle lines in ``services.student_profile``
(latest >= 75, latest < 45). Students with fewer than two percentages in any
subject stay unclassified.

Official passing rate, parent NPS, teacher ratings, concept mastery, social
connection, and regional benchmarks are not calculated. Those source
collections do not exist.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from dashboard.constants import (
    PRESSURE_PERCENTAGE,
    STRENGTH_PERCENTAGE,
    STRUGGLE_PERCENTAGE,
)
from dashboard.identity import (
    academic_year_of,
    as_datetime,
    class_id_of,
    grade_id_of,
    isoformat,
    mark_year,
    subject_id_of,
    subject_name,
)
from services.marks import DECLINE_DELTA, IMPROVE_DELTA, stored_percentage

SEVERITY_RANK = {None: 0, "watch": 1, "at_risk": 2, "critical": 3}
STORED_LEVELS = {
    "crisis": "critical",
    "critical": "critical",
    "high": "critical",
    "elevated": "at_risk",
    "moderate": "at_risk",
    "at_risk": "at_risk",
    "at-risk": "at_risk",
    "watch": "watch",
    "emerging": "watch",
    "low": None,
    "typical": None,
    "none": None,
    "": None,
}

QUADRANT_METHOD = {
    "id": "marks_delta_quadrant_v1",
    "minimum_points_per_subject": 2,
    "strength_percentage": STRENGTH_PERCENTAGE,
    "struggle_percentage": STRUGGLE_PERCENTAGE,
    "pressure_percentage": PRESSURE_PERCENTAGE,
    "decline_delta": DECLINE_DELTA,
    "improve_delta": IMPROVE_DELTA,
    "rules": [
        "Unclassified when no subject has two stored percentages.",
        "Stars: latest mean >= 75 and not declining (delta > -8).",
        "Plateaued: latest mean >= 75 and declining, or middle band that is not climbing or critical.",
        "Climbers: latest mean < 75 and delta >= 8.",
        "Critical: latest mean < 45, or latest mean < 75 and delta <= -8.",
    ],
}


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def mean(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def higher_severity(left: Optional[str], right: Optional[str]) -> Optional[str]:
    if SEVERITY_RANK.get(left, 0) >= SEVERITY_RANK.get(right, 0):
        return left
    return right


def trend_label(delta: Optional[float], *, samples: int) -> str:
    if delta is None or samples < 2:
        return "insufficient_data"
    if delta <= DECLINE_DELTA:
        return "declining"
    if delta >= IMPROVE_DELTA:
        return "improving"
    return "stable"


def severity_from_stored_level(value: Any) -> Optional[str]:
    if value is None:
        return None
    return STORED_LEVELS.get(str(value).strip().lower())


def _crisis(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return False


def severity_from_turn(turn: dict | None) -> Optional[str]:
    if not turn:
        return None
    band = str(turn.get("band") or "").strip().lower()
    if band:
        if band in STORED_LEVELS:
            mapped = STORED_LEVELS[band]
            if mapped or band in {"low", "typical", "none"}:
                return mapped
    score = turn.get("risk_intensity_score")
    if _crisis(turn.get("crisis_keywords")) or (is_number(score) and float(score) >= 8):
        return "critical"
    if is_number(score) and float(score) >= 5:
        return "at_risk"
    return None


def week_key(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _in_window(when: Optional[datetime], start: Optional[datetime], end: Optional[datetime]) -> bool:
    if start is None and end is None:
        return True
    if when is None:
        return False
    if start is not None and when < start:
        return False
    if end is not None and when > end:
        return False
    return True


class Scope:
    def __init__(
        self,
        *,
        academic_year: Optional[str] = None,
        grade_id: Optional[str] = None,
        class_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        from_at: Optional[datetime] = None,
        to_at: Optional[datetime] = None,
    ) -> None:
        self.academic_year = academic_year
        self.grade_id = grade_id
        self.class_id = class_id
        self.subject_id = subject_id
        self.from_at = from_at
        self.to_at = to_at

    def cache_token(self) -> str:
        return "|".join(
            [
                self.academic_year or "*",
                self.from_at.isoformat() if self.from_at else "*",
                self.to_at.isoformat() if self.to_at else "*",
            ]
        )


def student_matches(student: dict, scope: Scope) -> bool:
    if scope.grade_id and student.get("grade_id") != scope.grade_id:
        return False
    if scope.class_id and student.get("class_id") != scope.class_id:
        return False
    return True


def mark_in_scope(mark: dict, student: dict | None, scope: Scope) -> bool:
    if student is None or not student_matches(student, scope):
        return False
    if scope.subject_id and subject_id_of(mark.get("subject")) != scope.subject_id:
        return False
    if scope.academic_year and mark_year(mark) != scope.academic_year:
        return False
    return _in_window(as_datetime(mark.get("exam_date")), scope.from_at, scope.to_at)


def signal_in_scope(row: dict, student: dict | None, scope: Scope, when_field: str) -> bool:
    if student is None or not student_matches(student, scope):
        return False
    return _in_window(as_datetime(row.get(when_field)), scope.from_at, scope.to_at)


def subject_series(marks: list[dict]) -> dict[str, list[tuple[datetime, float, str]]]:
    grouped: dict[str, list[tuple[datetime, float, str]]] = defaultdict(list)
    for mark in marks:
        pct = stored_percentage(mark)
        name = subject_name(mark.get("subject"))
        if pct is None or not name:
            continue
        when = as_datetime(mark.get("exam_date")) or datetime.min.replace(tzinfo=timezone.utc)
        grouped[name].append((when, pct, subject_id_of(name)))
    for name in grouped:
        grouped[name].sort(key=lambda item: item[0])
    return grouped


def summarize_student_marks(marks: list[dict]) -> dict[str, Any]:
    series = subject_series(marks)
    latests: list[float] = []
    deltas: list[float] = []
    by_subject: dict[str, dict[str, Any]] = {}
    for name, points in series.items():
        latest = points[-1][1]
        latests.append(latest)
        delta = None
        if len(points) >= 2:
            delta = round(points[-1][1] - points[0][1], 2)
            deltas.append(delta)
        by_subject[name] = {
            "subject_id": points[-1][2],
            "latest": round(latest, 2),
            "delta": delta,
            "points": len(points),
            "trend": trend_label(delta, samples=len(points)),
        }
    return {
        "latest": mean(latests),
        "delta": mean(deltas) if deltas else None,
        "subjects": by_subject,
        "samples": sum(item["points"] for item in by_subject.values()),
    }


def classify_quadrant(latest: Optional[float], delta: Optional[float]) -> Optional[str]:
    """Return stars, plateaued, climbers, critical, or None when unclassified."""
    if latest is None or delta is None:
        return None
    declining = delta <= DECLINE_DELTA
    improving = delta >= IMPROVE_DELTA
    if latest >= STRENGTH_PERCENTAGE and not declining:
        return "stars"
    if latest >= STRENGTH_PERCENTAGE and declining:
        return "plateaued"
    if latest < STRENGTH_PERCENTAGE and improving:
        return "climbers"
    if latest < STRUGGLE_PERCENTAGE or (latest < STRENGTH_PERCENTAGE and declining):
        return "critical"
    return "plateaued"


def attendance_percentage(user: dict) -> Optional[float]:
    direct = user.get("attendance_percentage")
    if is_number(direct):
        return float(direct)
    raw = user.get("attendance")
    if is_number(raw):
        return float(raw)
    if isinstance(raw, dict):
        for key in ("percentage", "attendance_percentage"):
            if is_number(raw.get(key)):
                return float(raw[key])
    return None


def sleep_hours(row: dict) -> Optional[float]:
    minutes = row.get("total_duration_minutes")
    if not is_number(minutes) or float(minutes) < 0:
        return None
    return float(minutes) / 60.0


def physical_index(hours: list[float]) -> Optional[float]:
    """100 at 8 hours, falling 12.5 points per hour away from 8. No hours → null."""
    if not hours:
        return None
    scores = [max(0.0, 100.0 - abs(hour - 8.0) * 12.5) for hour in hours]
    return mean(scores)


def mental_index(scores: list[float]) -> Optional[float]:
    """Mood check-ins are stored from 1 to 10. The index is that mean times 10."""
    if not scores:
        return None
    return mean([max(1.0, min(10.0, score)) * 10.0 for score in scores])


def composite(parts: list[Optional[float]]) -> Optional[float]:
    present = [part for part in parts if part is not None]
    return mean(present)


def mood_delta(moods: list[dict], *, now: Optional[datetime] = None) -> Optional[float]:
    now = now or datetime.now(timezone.utc)
    recent_cut = now - timedelta(days=14)
    prior_cut = now - timedelta(days=28)
    recent: list[float] = []
    prior: list[float] = []
    for row in moods:
        if not is_number(row.get("score")):
            continue
        when = as_datetime(row.get("logged_at") or row.get("created_at"))
        if when is None:
            continue
        score = float(row["score"])
        if when >= recent_cut:
            recent.append(score)
        elif when >= prior_cut:
            prior.append(score)
    if len(recent) < 2 or len(prior) < 2:
        return None
    return round((mean(recent) or 0) - (mean(prior) or 0), 2)


def risk_for_student(
    student: dict,
    turn: dict | None,
    patterns: list[dict],
    *,
    min_confidence: float,
) -> dict[str, Any]:
    stored = severity_from_stored_level(student.get("current_risk_level"))
    turned = severity_from_turn(turn)
    severity = higher_severity(stored, turned)
    concern = None
    source = None
    if stored and SEVERITY_RANK[stored] == SEVERITY_RANK[severity] and stored == severity:
        source = "users.current_risk_level"
        concern = "stored_risk_level"
    if turned and (source is None or SEVERITY_RANK[turned] >= SEVERITY_RANK.get(severity, 0)):
        if turned == severity:
            source = "user_risk_turns"
            concern = "risk_turn"
    watch_pattern = None
    for pattern in patterns:
        status = str(pattern.get("status") or "")
        confidence = pattern.get("confidence")
        if status not in {"EMERGING", "ESTABLISHED"}:
            continue
        if not is_number(confidence) or float(confidence) < min_confidence:
            continue
        watch_pattern = pattern
        break
    if watch_pattern is not None:
        upgraded = higher_severity(severity, "watch")
        if upgraded == "watch" and severity is None:
            source = "user_patterns"
            concern = str(watch_pattern.get("pattern_type") or "pattern")
        elif concern is None and watch_pattern.get("pattern_type"):
            concern = str(watch_pattern.get("pattern_type"))
        severity = upgraded
    if watch_pattern is not None and source == "user_patterns":
        concern = str(watch_pattern.get("pattern_type") or concern or "pattern")
    last_at = None
    if turn:
        last_at = turn.get("created_at")
    for pattern in patterns:
        observed = pattern.get("last_observed_at")
        if as_datetime(observed) and (
            last_at is None or as_datetime(observed) > as_datetime(last_at)
        ):
            if concern and source == "user_patterns":
                last_at = observed
            elif source != "user_patterns" and as_datetime(observed):
                if last_at is None:
                    last_at = observed
    return {
        "severity": severity,
        "concern": concern,
        "source": source,
        "last_event_at": isoformat(last_at),
    }


def empty_quadrant() -> dict[str, Any]:
    return {
        "stars": {"count": 0, "students": [], "truncated": False},
        "plateaued": {"count": 0, "students": [], "truncated": False},
        "climbers": {"count": 0, "students": [], "truncated": False},
        "critical": {"count": 0, "students": [], "truncated": False},
        "unclassified": 0,
    }


def build_quadrant(rows: list[dict[str, Any]], *, cap: int) -> dict[str, Any]:
    board = empty_quadrant()
    for row in rows:
        bucket = row.get("quadrant")
        if bucket not in {"stars", "plateaued", "climbers", "critical"}:
            board["unclassified"] += 1
            continue
        board[bucket]["count"] += 1
        if len(board[bucket]["students"]) < cap:
            board[bucket]["students"].append(
                {
                    "student_id": row["student_id"],
                    "name": row.get("name") or "",
                    "latest_percentage": row.get("latest"),
                    "growth_delta": row.get("delta"),
                    "class_id": row.get("class_id"),
                }
            )
        else:
            board[bucket]["truncated"] = True
    return board


def unavailable(metric: str, reason: str) -> dict[str, Any]:
    return {"value": None, "available": False, "reason": reason}


PARENT_NPS_REASON = "No parent survey or NPS collection is stored."
PASS_RATE_REASON = (
    "Marks documents do not store a pass mark or board cutoff, so an official passing rate is not calculated."
)
SGP_REASON = (
    "A student growth percentile needs a normative cohort. "
    "Stored marks support a within-student percentage-point delta only."
)
SOCIAL_REASON = (
    "No social-connection instrument is stored. Pattern social-difficulty signals are distress counts "
    "and are not inverted into a connection score."
)
RATING_REASON = "No teacher-rating or student-feedback collection is stored."
CONCEPT_REASON = "Marks are stored per subject, not per concept or micro-topic."
BENCHMARK_REASON = "No regional, board, or peer-school benchmark collection is stored."
ASSIGNMENT_REASON = (
    "No school assignment collection is stored. daily_tasks are personal wellness tasks "
    "and are not treated as classwork."
)
TEACHER_PERFORMANCE_REASON = (
    "Marks rows do not include teacher_id, so a teacher performance score is not calculated."
)
ATTENDANCE_REASON = "No attendance percentage is stored on the student or the consolidated profile."


def insight_text(
    *,
    risk: dict[str, int],
    academic_trend: str,
    weakest_subject: Optional[str],
) -> Optional[str]:
    parts = []
    if risk.get("critical"):
        parts.append(
            f"{risk['critical']} students are in the critical band from stored risk levels or risk turns."
        )
    elif risk.get("at_risk"):
        parts.append(
            f"{risk['at_risk']} students are in the at-risk band from stored risk levels or risk turns."
        )
    if academic_trend == "declining":
        parts.append("The mean within-student mark delta is declining.")
    elif academic_trend == "improving":
        parts.append("The mean within-student mark delta is improving.")
    if weakest_subject:
        parts.append(f"The lowest subject average currently on file is {weakest_subject}.")
    if not parts:
        return None
    return " ".join(parts)


def index_student(user: dict) -> dict[str, Any]:
    return {
        "user_id": str(user.get("user_id") or ""),
        "name": user.get("name") or "",
        "email": user.get("email"),
        "class_label": user.get("class_label") or "",
        "class_id": user.get("class_id") or class_id_of(user),
        "grade_id": user.get("grade_id") or grade_id_of(user),
        "roles": list(user.get("roles") or []),
        "current_risk_level": user.get("current_risk_level"),
        "board": user.get("board") or user.get("school_board"),
        "attendance_percentage": attendance_percentage(user),
        "subjects": user.get("subjects") or [],
        "classes": user.get("classes") or [],
        "parent_on_file": bool(user.get("parent_on_file")),
    }

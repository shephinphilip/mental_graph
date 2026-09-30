"""
Personal sleep patterns. Facts, observations, and repeated co-occurrences.

Nothing here claims that sleep caused another outcome.
"""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import pstdev
from typing import Any, Dict, List, Optional, Sequence

from sleep.reader import parse_clock, stored_duration

_NEGATIVE_MOODS = {
    "anxious",
    "sad",
    "low",
    "stressed",
    "stress",
    "down",
    "tired",
    "overwhelmed",
    "irritable",
    "flat",
    "hopeless",
}
_POSITIVE_MOODS = {"calm", "okay", "ok", "happy", "steady", "good", "fine"}


def _minutes(records: Sequence[Dict[str, Any]]) -> List[int]:
    values = []
    for row in records:
        minutes = stored_duration(row)
        if minutes is not None:
            values.append(minutes)
    return values


def baseline_observation(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    """
    Latest night versus this person's other recent nights.

    Needs at least three earlier nights. No population norm.
    """
    if len(records) < 4:
        return None
    latest = stored_duration(records[0])
    earlier = _minutes(list(records)[1:])
    if latest is None or len(earlier) < 3:
        return None
    average = sum(earlier) / len(earlier)
    gap = average - latest
    if gap < 45:
        return None
    return (
        f"[OBSERVATION] The latest logged night ({_fmt(latest)}) is below "
        f"this person's recent average ({_fmt(int(round(average)))}). "
        "This is a comparison with their own logs, not a general standard."
    )


def duration_trend(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    """Recent nights lower than this person's earlier nights in the same window."""
    values = _minutes(records)
    if len(values) < 6:
        return None
    # History is newest first.
    recent = values[:3]
    earlier = values[3:]
    if sum(recent) / len(recent) > (sum(earlier) / len(earlier)) - 45:
        return None
    return (
        "[PATTERN] Recent sleep duration has repeatedly been lower than "
        "this person's own earlier nights in the same window. "
        "That is a trend in the logs, not a cause of anything else."
    )


def irregular_schedule(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    clocks = [parse_clock(row.get("bedtime")) for row in records]
    clocks = [item for item in clocks if item is not None]
    if len(clocks) < 4:
        return None
    # Circular-ish spread: treat very late nights as next-day minutes past 18:00.
    shifted = [item + 24 * 60 if item < 12 * 60 else item for item in clocks]
    if pstdev(shifted) < 90:
        return None
    return (
        "[PATTERN] Bedtime varies widely across recent logged nights. "
        "This describes the schedule, not its effect."
    )


def _low_dates(records: Sequence[Dict[str, Any]]) -> set:
    values = _minutes(records)
    if len(values) < 4:
        return set()
    ordered = sorted(values)
    median = ordered[len(ordered) // 2]
    dates = set()
    for row in records:
        minutes = stored_duration(row)
        if minutes is not None and minutes <= median - 30:
            dates.add(str(row.get("date")))
    return dates


def mood_coincidence(
    records: Sequence[Dict[str, Any]],
    moods: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Repeated overlap between shorter nights and more negative mood check-ins.

    Equal overlap with positive moods is contradictory, so no pattern is emitted.
    """
    low_dates = _low_dates(records)
    if len(low_dates) < 3 or not moods:
        return None
    negative = 0
    positive = 0
    for mood in moods:
        day = str(mood.get("date") or "")[:10]
        if day not in low_dates:
            continue
        label = str(mood.get("value") or mood.get("mood") or "").lower()
        if any(word in label for word in _NEGATIVE_MOODS):
            negative += 1
        elif any(word in label for word in _POSITIVE_MOODS):
            positive += 1
    if negative < 3:
        return None
    if positive >= negative:
        return {
            "level": "contradiction",
            "text": (
                "[OBSERVATION] Shorter nights have coincided with both "
                "heavier and lighter mood check-ins, so no mood pattern is kept."
            ),
            "evidence": negative,
            "contradictions": positive,
        }
    return {
        "level": "pattern",
        "text": (
            "[PATTERN] Shorter sleep has repeatedly coincided with more negative "
            "mood check-ins. These happened together. Sleep is not described as the cause."
        ),
        "evidence": negative,
        "contradictions": positive,
    }


def task_coincidence(
    records: Sequence[Dict[str, Any]],
    tasks: Sequence[Dict[str, Any]],
) -> Optional[str]:
    low_dates = _low_dates(records)
    if len(low_dates) < 3:
        return None
    hits = 0
    for task in tasks:
        day = str(task.get("date") or "")[:10]
        pending = task.get("pending")
        try:
            pending_n = int(pending)
        except (TypeError, ValueError):
            pending_n = 1 if task.get("incomplete") else 0
        if day in low_dates and pending_n > 0:
            hits += 1
    if hits < 3:
        return None
    return (
        "[PATTERN] Shorter sleep has repeatedly coincided with more "
        "pending or incomplete tasks. That is a repeated overlap, not a cause."
    )


def academic_coincidence(
    records: Sequence[Dict[str, Any]],
    academic: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """Low marks landing on dates that also had shorter sleep. Needs repetition."""
    low_dates = _low_dates(records)
    if len(low_dates) < 3:
        return None
    hits = 0
    for row in academic:
        day = str(row.get("date") or row.get("exam_date") or "")[:10]
        try:
            score = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if day in low_dates and score < 50:
            hits += 1
    if hits < 3:
        return None
    return (
        "[PATTERN] Periods of shorter sleep have repeatedly coincided with "
        "lower recorded marks. The logs overlap. They do not show that sleep "
        "produced the marks."
    )


def meditation_feedback(
    records: Sequence[Dict[str, Any]],
    executions: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """Explicit helpfulness after a sleep-tagged practice. Not an inferred sleep improvement."""
    helpful = [
        row
        for row in executions
        if str(row.get("user_helpfulness_feedback") or "").upper() == "HELPFUL"
        and (
            row.get("category") == "sleep"
            or "SLEEP" in str(row.get("technique") or "").upper()
            or row.get("sleep_practice") is True
        )
    ]
    if len(helpful) < 2 or not records:
        return None
    return (
        "[PATTERN] After sleep-focused meditation sessions, this person "
        "explicitly said the practice was helpful. That is their feedback, "
        "not evidence that the practice changed their sleep."
    )


def meditation_support(
    records: Sequence[Dict[str, Any]],
    top_state: str,
    time_bucket: str = "",
) -> Dict[str, bool]:
    """
    Supporting hint for the existing ranker.

    A short night does not by itself select a sleep practice.
    A calm reading is left unchanged.
    """
    below = baseline_observation(records) is not None
    calm = top_state == "CALM"
    fatigue = top_state in {"COGNITIVE_FATIGUE", "OVERWHELM_HIGH", "STRESS_HIGH"}
    preparing = top_state == "SLEEP_PREPARATION"
    evening = time_bucket in {"evening", "night"}
    return {
        "relevant": (not calm) and (fatigue or preparing),
        "prefer_sleep": (not calm) and preparing and (evening or below),
        "prefer_low_effort": (not calm) and below and fatigue,
    }


def hypothesis_line(has_pattern: bool) -> Optional[str]:
    if not has_pattern:
        return None
    return (
        "[HYPOTHESIS] Shorter sleep may be worth asking about when focus "
        "or mood is hard. Ask if they see a connection. Do not assert one."
    )


def _fmt(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m"


def findings(
    records: Sequence[Dict[str, Any]],
    *,
    moods: Sequence[Dict[str, Any]] = (),
    tasks: Sequence[Dict[str, Any]] = (),
    academic: Sequence[Dict[str, Any]] = (),
    executions: Sequence[Dict[str, Any]] = (),
) -> List[Dict[str, Any]]:
    """Structured findings the pattern store can turn into candidates."""
    out: List[Dict[str, Any]] = []
    observation = baseline_observation(records)
    if observation:
        out.append({"key": "sleep_below_personal_baseline", "level": "observation", "text": observation, "evidence": 1, "contradictions": 0})
    trend = duration_trend(records)
    if trend:
        out.append({"key": "sleep_duration_trend", "level": "pattern", "text": trend, "evidence": 3, "contradictions": 0})
    schedule = irregular_schedule(records)
    if schedule:
        out.append({"key": "sleep_schedule_irregular", "level": "pattern", "text": schedule, "evidence": 4, "contradictions": 0})
    mood = mood_coincidence(records, moods)
    if mood and mood["level"] == "pattern":
        out.append({"key": "sleep_mood_coincidence", **mood})
    tasks_text = task_coincidence(records, tasks)
    if tasks_text:
        out.append({"key": "sleep_task_coincidence", "level": "pattern", "text": tasks_text, "evidence": 3, "contradictions": 0})
    academic_text = academic_coincidence(records, academic)
    if academic_text:
        out.append({"key": "sleep_academic_coincidence", "level": "pattern", "text": academic_text, "evidence": 3, "contradictions": 0})
    practice = meditation_feedback(records, executions)
    if practice:
        out.append({"key": "sleep_meditation_feedback", "level": "pattern", "text": practice, "evidence": 2, "contradictions": 0})
    guess = hypothesis_line(any(item["level"] == "pattern" for item in out))
    if guess:
        out.append({"key": "sleep_hypothesis", "level": "hypothesis", "text": guess, "evidence": 1, "contradictions": 0})
    return out

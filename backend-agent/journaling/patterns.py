"""
Journal patterns from repeated entries. Correlations are not causes.

Facts stay on the journal row. These strings are observations and patterns.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from journaling.models import NEGATIVE_MOODS, POSITIVE_MOODS


def mood_trend(entries: Sequence[Dict[str, Any]]) -> Optional[str]:
    if len(entries) < 4:
        return None
    sad = sum(1 for row in entries if row.get("mood") in NEGATIVE_MOODS)
    if sad / len(entries) < 0.6:
        return None
    return (
        "[OBSERVATION] The mood selected on recent journal entries has mostly "
        "been 😢. That is what was chosen, not a measured emotion."
    )


def recurring_topics(entries: Sequence[Dict[str, Any]]) -> List[str]:
    counts: Counter = Counter()
    for row in entries:
        for topic in row.get("topics") or []:
            counts[str(topic)] += 1
    lines = []
    for topic, count in counts.most_common():
        if count < 3:
            continue
        lines.append(
            f"[PATTERN] {topic} shows up in {count} recent journal entries. "
            "That is a repeated topic, not a diagnosis."
        )
    return lines


def _negative_dates(entries: Sequence[Dict[str, Any]]) -> set:
    return {str(row.get("date")) for row in entries if row.get("mood") in NEGATIVE_MOODS and row.get("date")}


def sleep_coincidence(
    entries: Sequence[Dict[str, Any]],
    sleep_rows: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Negative selected moods overlapping shorter nights. Equal positive overlap is dropped."""
    if len(entries) < 3 or len(sleep_rows) < 3:
        return None
    durations = []
    by_date = {}
    for row in sleep_rows:
        try:
            minutes = int(row.get("value") if row.get("value") is not None else row.get("total_duration_minutes"))
        except (TypeError, ValueError):
            continue
        day = str(row.get("date") or "")
        if not day:
            continue
        durations.append(minutes)
        by_date[day] = minutes
    if len(durations) < 3:
        return None
    median = sorted(durations)[len(durations) // 2]
    short_dates = {day for day, minutes in by_date.items() if minutes <= median - 30}
    negative = 0
    positive = 0
    for row in entries:
        if str(row.get("date")) not in short_dates:
            continue
        if row.get("mood") in NEGATIVE_MOODS:
            negative += 1
        elif row.get("mood") in POSITIVE_MOODS:
            positive += 1
    if negative < 3:
        return None
    if positive >= negative:
        return {"level": "contradiction", "text": "", "evidence": negative, "contradictions": positive}
    return {
        "level": "pattern",
        "text": (
            "[PATTERN] More negative selected journal moods have repeatedly "
            "landed on days with shorter sleep. They overlapped. Sleep is not "
            "described as the cause."
        ),
        "evidence": negative,
        "contradictions": positive,
    }


def task_coincidence(
    entries: Sequence[Dict[str, Any]],
    tasks: Sequence[Dict[str, Any]],
) -> Optional[str]:
    negative_dates = _negative_dates(entries)
    if len(negative_dates) < 3:
        return None
    hits = 0
    for task in tasks:
        day = str(task.get("date") or "")[:10]
        try:
            pending = int(task.get("pending"))
        except (TypeError, ValueError):
            pending = 1 if task.get("incomplete") else 0
        if day in negative_dates and pending > 0:
            hits += 1
    if hits < 3:
        return None
    return (
        "[PATTERN] Negative selected journal moods have repeatedly coincided "
        "with pending tasks. That is a repeated overlap, not a cause."
    )


def academic_coincidence(
    entries: Sequence[Dict[str, Any]],
    academic: Sequence[Dict[str, Any]],
) -> Optional[str]:
    exam_dates = {
        str(row.get("date"))
        for row in entries
        if row.get("date") and any(topic in {"exam", "test", "marks", "physics"} for topic in (row.get("topics") or []))
    }
    if len(exam_dates) < 3 or not academic:
        return None
    hits = 0
    for row in academic:
        day = str(row.get("date") or "")[:10]
        try:
            score = float(row.get("value"))
        except (TypeError, ValueError):
            continue
        if day in exam_dates and score < 50:
            hits += 1
    if hits < 3:
        return None
    return (
        "[PATTERN] Exam or assessment language in the journal has repeatedly "
        "overlapped with lower recorded marks. The logs overlap. They do not "
        "show that one produced the other."
    )


def meditation_sequence(
    entries: Sequence[Dict[str, Any]],
    feedback: Sequence[Dict[str, Any]],
) -> Optional[str]:
    helpful_days = sorted(str(row.get("date")) for row in feedback if row.get("date") and row.get("helpful"))
    if len(helpful_days) < 2:
        return None
    later_positive = 0
    for day in helpful_days:
        if any(row.get("date", "") >= day and row.get("mood") in POSITIVE_MOODS for row in entries):
            later_positive += 1
    if later_positive < 2:
        return None
    return (
        "[PATTERN] After a meditation marked helpful, a later journal entry "
        "sometimes had a more positive selected mood. That is a sequence in "
        "the logs, not evidence the practice caused the mood."
    )


def findings(
    entries: Sequence[Dict[str, Any]],
    *,
    sleep_rows: Sequence[Dict[str, Any]] = (),
    tasks: Sequence[Dict[str, Any]] = (),
    academic: Sequence[Dict[str, Any]] = (),
    feedback: Sequence[Dict[str, Any]] = (),
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    trend = mood_trend(entries)
    if trend:
        out.append({"key": "journal_mood_trend", "level": "observation", "text": trend, "evidence": 4, "contradictions": 0})
    for index, line in enumerate(recurring_topics(entries)):
        out.append(
            {
                "key": f"journal_topic:{index}",
                "level": "pattern",
                "text": line,
                "evidence": 3,
                "contradictions": 0,
            }
        )
    sleep = sleep_coincidence(entries, sleep_rows)
    if sleep and sleep["level"] == "pattern":
        out.append({"key": "journal_sleep_coincidence", **sleep})
    tasks_text = task_coincidence(entries, tasks)
    if tasks_text:
        out.append({"key": "journal_task_coincidence", "level": "pattern", "text": tasks_text, "evidence": 3, "contradictions": 0})
    academic_text = academic_coincidence(entries, academic)
    if academic_text:
        out.append({"key": "journal_academic_coincidence", "level": "pattern", "text": academic_text, "evidence": 3, "contradictions": 0})
    practice = meditation_sequence(entries, feedback)
    if practice:
        out.append({"key": "journal_meditation_sequence", "level": "pattern", "text": practice, "evidence": 2, "contradictions": 0})
    if any(item["level"] == "pattern" for item in out):
        out.append(
            {
                "key": "journal_hypothesis",
                "level": "hypothesis",
                "text": (
                    "[HYPOTHESIS] A repeated overlap may be worth asking about. "
                    "Ask if they see a connection. Do not assert one."
                ),
                "evidence": 1,
                "contradictions": 0,
            }
        )
    return out

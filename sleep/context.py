"""Compact sleep block for prompts. Not a raw collection dump."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config import get_settings
from sleep.patterns import baseline_observation, findings
from sleep.reader import get_recent_sleep, get_sleep_history, stored_duration, valid_records

logger = logging.getLogger(__name__)

EMPTY_SLEEP_CONTEXT = "No sleep data available"

_RELEVANCE = (
    "Use this only when the current message is about energy, focus, mood, "
    "stress, rest, or the night. If the message is about something else, "
    "do not mention sleep. Never say sleep caused a feeling or a result. "
    "If you do mention it, describe what was logged and what has coincided "
    "for this person, and let them agree or disagree."
)


def format_duration(minutes: int) -> str:
    hours, mins = divmod(int(minutes), 60)
    return f"{hours}h {mins:02d}m"


def format_sleep_context(records: List[Dict[str, Any]]) -> str:
    """Bounded recent nights plus a personal-baseline observation when one exists."""
    if not records:
        return EMPTY_SLEEP_CONTEXT
    latest = records[0]
    minutes = stored_duration(latest)
    lines = [
        "RECENT SLEEP",
        "Facts are logged times and durations. Observations compare this person to themself.",
        "",
        "Last night:",
        f"- Bedtime: {latest.get('bedtime')}",
        f"- Wake time: {latest.get('wake_up_time')}",
        f"- Duration: {format_duration(minutes) if minutes else 'unknown'}",
        "",
        "Recent history:",
    ]
    for row in records:
        row_minutes = stored_duration(row)
        if row_minutes is None:
            continue
        lines.append(f"- {row.get('date')} → {format_duration(row_minutes)}")
    observation = baseline_observation(records)
    if observation:
        lines.extend(["", "Observation:", observation])
    for item in findings(records):
        if item.get("key") == "sleep_below_personal_baseline":
            continue
        lines.append(item["text"])
    lines.extend(["", "Relevance:", _RELEVANCE])
    text = "\n".join(lines)
    if "_id" in text or "ObjectId" in text:
        raise RuntimeError("sleep context leaked storage metadata")
    return text


async def build_sleep_context(db, user_id: str) -> str:
    """Fail open. Chat continues when the sleep read fails."""
    try:
        settings = get_settings()
        history = await get_sleep_history(db, user_id, days=settings.SLEEP_CONTEXT_DAYS)
        records = valid_records(history)
        if not records:
            recent = await get_recent_sleep(db, user_id)
            records = valid_records([recent] if recent else [])
        return format_sleep_context(records)
    except Exception:
        logger.exception("Sleep context failed for user=%s", user_id)
        return EMPTY_SLEEP_CONTEXT

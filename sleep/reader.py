"""
sleep_logs readers.

get_recent_sleep: latest document by created_at, no date filter.
get_sleep_history: created_at >= now - days, newest first, no product limit.

Invalid rows stay in those query results. AI context uses valid_records().
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sleep.identity import identity_keys, identity_query

_CLOCK = re.compile(
    r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*$",
    re.IGNORECASE,
)
MAX_DURATION_MINUTES = 18 * 60
# A bedtime before this belongs to the previous night's sleep cycle.
EARLY_MORNING_CUTOFF_MINUTES = 6 * 60


def parse_clock(value: Any) -> Optional[int]:
    """Minutes from midnight, or None if the free-text time cannot be read."""
    match = _CLOCK.match(str(value or ""))
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    suffix = (match.group(3) or "").lower()
    if minute > 59 or hour > 23:
        return None
    if suffix:
        if hour < 1 or hour > 12:
            return None
        if hour == 12:
            hour = 0
        if suffix == "pm":
            hour += 12
    return hour * 60 + minute


def duration_from_times(bedtime: Any, wake_up_time: Any) -> Optional[int]:
    """
    Minutes asleep. Wake earlier on the clock than bedtime crosses midnight.

    11:00 PM → 7:15 AM is 8h 15m, not a negative same-day gap.
    """
    start = parse_clock(bedtime)
    end = parse_clock(wake_up_time)
    if start is None or end is None:
        return None
    if end <= start:
        end += 24 * 60
    minutes = end - start
    if minutes <= 0 or minutes > MAX_DURATION_MINUTES:
        return None
    return minutes


def sleep_cycle_date(date: str, bedtime: Any) -> str:
    """
    The night the sleep belongs to, not the calendar morning after midnight.

    Logging a 1:30 AM bedtime on 2026-09-23 stores 2026-09-22.
    An evening bedtime stays on the date that was sent.
    The caller should pass the calendar date of the log, usually today.
    """
    logged = datetime.strptime(str(date), "%Y-%m-%d").date()
    minutes = parse_clock(bedtime)
    if minutes is not None and minutes < EARLY_MORNING_CUTOFF_MINUTES:
        logged = logged - timedelta(days=1)
    return logged.isoformat()


def stored_duration(doc: Dict[str, Any]) -> Optional[int]:
    """Prefer total_duration_minutes. Do not invent a number the row does not support."""
    raw = doc.get("total_duration_minutes")
    if raw is not None and raw != "":
        try:
            minutes = int(raw)
        except (TypeError, ValueError):
            return None
        if minutes <= 0 or minutes > MAX_DURATION_MINUTES:
            return None
        return minutes
    return duration_from_times(doc.get("bedtime"), doc.get("wake_up_time"))


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def record_problem(doc: Dict[str, Any], *, now: Optional[datetime] = None) -> Optional[str]:
    """Why a row must be left out of AI context. None means usable."""
    now = now or datetime.now(timezone.utc)
    if not str(doc.get("user_id") or "").strip():
        return "missing_user_id"
    if _as_dt(doc.get("created_at")) is None:
        return "missing_created_at"
    if stored_duration(doc) is None:
        return "missing_or_impossible_duration"
    date = str(doc.get("date") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        return "bad_date"
    try:
        logged = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return "bad_date"
    if logged.date() > now.date():
        return "future_date"
    return None


def valid_records(docs: List[Dict[str, Any]], *, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Drop invalid rows and keep one row per date — the latest created_at."""
    usable = [doc for doc in docs if record_problem(doc, now=now) is None]
    usable.sort(key=lambda doc: _as_dt(doc.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
    by_date: Dict[str, Dict[str, Any]] = {}
    for doc in usable:
        by_date[str(doc.get("date"))] = doc
    chosen = list(by_date.values())
    chosen.sort(
        key=lambda doc: _as_dt(doc.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return chosen


async def _fetch(db, keys: List[str], *, since: Optional[datetime] = None, limit: Optional[int] = None):
    query = identity_query(keys)
    if since is not None:
        query["created_at"] = {"$gte": since}
    cursor = db["sleep_logs"].find(query)
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("created_at", -1)
    if limit is not None and hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    length = limit or 5000
    docs = await cursor.to_list(length=length)
    return [doc for doc in docs if isinstance(doc, dict)]


async def get_recent_sleep(db, user_id: str) -> Optional[Dict[str, Any]]:
    """Most recent sleep document for this authenticated identity. No date filter."""
    keys = await identity_keys(db, user_id)
    docs = await _fetch(db, keys, limit=1)
    return docs[0] if docs else None


async def get_sleep_history(db, user_id: str, days: int = 7) -> List[Dict[str, Any]]:
    """Rows with created_at within the last `days`, newest first. No product limit."""
    keys = await identity_keys(db, user_id)
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    return await _fetch(db, keys, since=since)

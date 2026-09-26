"""habit_events: one row per habit, with the days it was completed.

A missed day does not end a streak. Streaks are hidden until the person asks
for them, so the prompt stays quiet by default and nobody is nagged by a number.
"""

from __future__ import annotations

import logging
import secrets
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from config import get_settings
from tasks.identity import identity_keys, identity_query, owns_claimed_id
from tracking.indexes import HABITS

logger = logging.getLogger(__name__)

TITLE_MAX = 60
FREQUENCIES = ("daily", "weekly")
STATUSES = ("active", "paused", "archived")
MAX_COMPLETIONS = 400


def new_habit_id() -> str:
    return f"habit_{secrets.token_hex(4)}"


def clean_title(value: Any) -> str:
    text = " ".join(str(value or "").split())[:TITLE_MAX].strip()
    if len(text) < 2:
        raise ValueError("A habit needs a name")
    return text


def clean_frequency(value: Any) -> str:
    """Stored lowercase because the prompt reads this word out as-is."""
    text = str(value or "daily").strip().lower()
    if text not in FREQUENCIES:
        raise ValueError("Frequency must be daily or weekly")
    return text


def clean_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in STATUSES:
        raise ValueError("Unsupported status")
    return text


def clean_reminder(value: Any) -> str:
    """'HH:MM', 24 hour. The person picks the time; we never default one."""
    text = str(value or "").strip()
    if not text:
        return ""
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("Reminder must look like HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("Reminder must look like HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Reminder must look like HH:MM")
    return f"{hour:02d}:{minute:02d}"


def _today(now: Optional[datetime] = None) -> date:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date()


def _iso_week(day: date) -> tuple:
    year, week, _ = day.isocalendar()
    return (year, week)


def current_streak(
    completions: Iterable[str],
    frequency: str = "daily",
    *,
    today: Optional[date] = None,
    grace: Optional[int] = None,
) -> int:
    """
    Count the run of kept days, forgiving the odd miss.

    Up to `grace` missed days are allowed inside any trailing seven examined
    days. Two misses close together end the run; one does not.
    """
    today = today or _today()
    grace = get_settings().HABIT_GRACE_MISSES_PER_WEEK if grace is None else grace
    done = {str(item) for item in completions if item}
    if not done:
        return 0

    if str(frequency or "").strip().lower() == "weekly":
        weeks = set()
        for item in done:
            try:
                weeks.add(_iso_week(date.fromisoformat(item)))
            except ValueError:
                continue
        cursor = today
        if _iso_week(cursor) not in weeks:
            # The current week is still open; do not punish it yet.
            cursor = cursor - timedelta(days=7)
        streak = 0
        while _iso_week(cursor) in weeks and streak < 520:
            streak += 1
            cursor = cursor - timedelta(days=7)
        return streak

    window: deque = deque(maxlen=7)
    streak = 0
    cursor = today
    for _ in range(MAX_COMPLETIONS):
        hit = cursor.isoformat() in done
        window.append(hit)
        if not hit and list(window).count(False) > grace:
            break
        if hit:
            streak += 1
        cursor = cursor - timedelta(days=1)
    return streak


async def _streaks_visible(db, user_id: str) -> bool:
    try:
        from services.users import get_by_identifier

        user = await get_by_identifier(db, user_id)
    except Exception:
        logger.exception("Could not read streak preference for user=%s", user_id)
        return False
    return bool(isinstance(user, dict) and user.get("show_streaks"))


def public_habit(doc: Dict[str, Any], *, show_streaks: bool) -> Dict[str, Any]:
    habit = {
        "habit_id": doc.get("habit_id"),
        "title": doc.get("title"),
        "frequency": doc.get("frequency") or "daily",
        "status": doc.get("status") or "active",
        "reminder_time": doc.get("reminder_time") or "",
        "completed_today": _today().isoformat() in set(doc.get("completions") or []),
    }
    if show_streaks:
        habit["streak"] = int(doc.get("streak_internal") or 0)
    else:
        habit["streak_hidden"] = True
    return habit


async def create_habit(
    db,
    user_id: str,
    *,
    title: str,
    frequency: Any = "daily",
    reminder_time: Any = "",
    claimed_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot create a habit for another user")
    settings = get_settings()
    active = await db[HABITS].find({**identity_query(keys), "status": "active"}).to_list(length=100)
    if len(active) >= settings.HABIT_MAX_ACTIVE:
        raise ValueError("That is as many active habits as this list holds")
    now = now or datetime.now(timezone.utc)
    doc = {
        "habit_id": new_habit_id(),
        "user_id": user_id,
        "title": clean_title(title),
        "frequency": clean_frequency(frequency),
        "reminder_time": clean_reminder(reminder_time),
        "status": "active",
        "completions": [],
        "streak_internal": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db[HABITS].insert_one(doc)
    return public_habit(doc, show_streaks=await _streaks_visible(db, user_id))


async def list_habits(
    db,
    user_id: str,
    *,
    include_archived: bool = False,
    claimed_user_id: Optional[str] = None,
) -> Dict[str, Any]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot read another user's habits")
    query: Dict[str, Any] = dict(identity_query(keys))
    if not include_archived:
        query["status"] = {"$ne": "archived"}
    docs = await db[HABITS].find(query).to_list(length=100)
    show = await _streaks_visible(db, user_id)
    return {
        "show_streaks": show,
        "habits": [public_habit(doc, show_streaks=show) for doc in docs],
    }


async def _load_habit(db, keys: List[str], habit_id: str) -> Dict[str, Any]:
    doc = await db[HABITS].find_one({**identity_query(keys), "habit_id": habit_id})
    if not doc:
        raise LookupError("Habit not found")
    return doc


async def update_habit(
    db,
    user_id: str,
    habit_id: str,
    *,
    title: Optional[str] = None,
    frequency: Optional[str] = None,
    reminder_time: Optional[str] = None,
    status: Optional[str] = None,
    claimed_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Rename, retime, pause, or archive. Completion history is never rewritten."""
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot change another user's habit")
    doc = await _load_habit(db, keys, habit_id)
    changes: Dict[str, Any] = {"updated_at": now or datetime.now(timezone.utc)}
    if title is not None:
        changes["title"] = clean_title(title)
    if frequency is not None:
        changes["frequency"] = clean_frequency(frequency)
    if reminder_time is not None:
        changes["reminder_time"] = clean_reminder(reminder_time)
    if status is not None:
        changes["status"] = clean_status(status)
    show = await _streaks_visible(db, user_id)
    if changes.get("status") in {"paused", "archived"} or not show:
        # The prompt reads `streak`; keep it absent unless it is wanted on screen.
        changes["streak"] = 0
    await db[HABITS].update_one(
        {"user_id": doc.get("user_id"), "habit_id": habit_id}, {"$set": changes}
    )
    return public_habit({**doc, **changes}, show_streaks=show)


async def check_in(
    db,
    user_id: str,
    habit_id: str,
    *,
    on_date: Optional[str] = None,
    claimed_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Mark a habit kept for a day. Doing it twice changes nothing.

    This is the person's own record. It is not reported as a helpful outcome
    to adaptive memory.
    """
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot check in another user's habit")
    doc = await _load_habit(db, keys, habit_id)
    if (doc.get("status") or "active") == "archived":
        raise ValueError("That habit is archived")

    today = _today(now)
    if on_date:
        try:
            day = date.fromisoformat(str(on_date))
        except ValueError as exc:
            raise ValueError("Date must look like YYYY-MM-DD") from exc
        if day > today:
            day = today
        oldest = today - timedelta(days=get_settings().MOOD_BACKFILL_MAX_DAYS)
        if day < oldest:
            day = oldest
    else:
        day = today

    completions = sorted({*(doc.get("completions") or []), day.isoformat()})[-MAX_COMPLETIONS:]
    already = day.isoformat() in set(doc.get("completions") or [])
    streak = current_streak(completions, doc.get("frequency") or "daily", today=today)
    show = await _streaks_visible(db, user_id)
    changes: Dict[str, Any] = {
        "completions": completions,
        "streak_internal": streak,
        "last_completed_on": day.isoformat(),
        "updated_at": now or datetime.now(timezone.utc),
    }
    changes["streak"] = streak if show else 0
    await db[HABITS].update_one(
        {"user_id": doc.get("user_id"), "habit_id": habit_id}, {"$set": changes}
    )
    return {
        "habit": public_habit({**doc, **changes}, show_streaks=show),
        "already_logged": already,
    }


async def set_streak_visibility(db, user_id: str, visible: bool) -> Dict[str, Any]:
    """Opt in or out of seeing streaks. Opting out also hides them from the AI."""
    await db["users"].update_one(
        {"user_id": user_id}, {"$set": {"show_streaks": bool(visible)}}
    )
    keys = await identity_keys(db, user_id)
    docs = await db[HABITS].find(identity_query(keys)).to_list(length=100)
    for doc in docs:
        mirrored = int(doc.get("streak_internal") or 0) if visible else 0
        if (doc.get("status") or "active") != "active":
            mirrored = 0
        await db[HABITS].update_one(
            {"user_id": doc.get("user_id"), "habit_id": doc.get("habit_id")},
            {"$set": {"streak": mirrored}},
        )
    return {"show_streaks": bool(visible), "habits_updated": len(docs)}

"""mood_logs: one row per check-in.

A check-in is a single tap. Everything else is optional. The row carries both
`logged_at` (what the prompt reader sorts on) and `created_at` (what the
pattern engine windows on) so a backfilled entry lands on the day it happened.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import get_settings
from services.apm import contains_crisis_signal
from tasks.identity import identity_keys, identity_query, owns_claimed_id
from tracking.indexes import MOODS

logger = logging.getLogger(__name__)

MOOD_MAX = 40
NOTE_MAX = 280
INPUT_FORMATS = ("EMOJI", "SLIDER", "VOICE", "TEXT")


def clean_mood(value: Any) -> str:
    text = " ".join(str(value or "").split())[:MOOD_MAX].strip()
    if not text:
        raise ValueError("A mood is required")
    return text


def clamp_score(value: Any) -> Optional[int]:
    """1 to 10. A slider that sends nothing is fine."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return max(1, min(10, int(round(number))))


def clean_note(value: Any) -> str:
    return " ".join(str(value or "").split())[:NOTE_MAX].strip()


def clean_format(value: Any) -> str:
    text = str(value or "TEXT").strip().upper()
    return text if text in INPUT_FORMATS else "TEXT"


def resolve_logged_at(value: Any, *, now: Optional[datetime] = None) -> datetime:
    """
    Accept an offline backfill, within reason.

    A future timestamp is pulled back to now. Anything older than the backfill
    window is clamped rather than rejected, so a long-offline phone still syncs.
    """
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=get_settings().MOOD_BACKFILL_MAX_DAYS)
    if not isinstance(value, datetime):
        return now
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if when > now:
        return now
    return max(when, oldest)


def public_mood(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mood": doc.get("mood"),
        "score": doc.get("score"),
        "note": doc.get("note") or "",
        "input_format": doc.get("input_format") or "TEXT",
        "logged_at": doc.get("logged_at"),
    }


async def log_mood(
    db,
    user_id: str,
    *,
    mood: str,
    score: Any = None,
    note: Any = "",
    input_format: Any = "TEXT",
    client_event_id: Optional[str] = None,
    logged_at: Any = None,
    claimed_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Store one check-in.

    A repeated `client_event_id` returns the original row. A note carrying a
    crisis signal is still stored, flagged so the caller can surface help, and
    left out of pattern learning.
    """
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot log a mood for another user")

    label = clean_mood(mood)
    text = clean_note(note)
    now = now or datetime.now(timezone.utc)
    when = resolve_logged_at(logged_at, now=now)
    event_id = str(client_event_id).strip() if client_event_id else None

    if event_id:
        existing = await db[MOODS].find_one({"user_id": user_id, "client_event_id": event_id})
        if existing:
            return {
                "entry": public_mood(existing),
                "duplicate": True,
                "crisis": bool(existing.get("crisis_flagged")),
            }

    crisis = contains_crisis_signal(text) if text else False
    doc = {
        "user_id": user_id,
        "mood": label,
        "score": clamp_score(score),
        "note": text,
        "input_format": clean_format(input_format),
        "logged_at": when,
        "created_at": when,
        "synced_at": now,
        "crisis_flagged": crisis,
    }
    if event_id:
        doc["client_event_id"] = event_id
    await db[MOODS].insert_one(doc)
    if crisis:
        logger.warning("Crisis wording in a mood note — user=%s", user_id)
    return {"entry": public_mood(doc), "duplicate": False, "crisis": crisis}


async def recent_moods(
    db,
    user_id: str,
    *,
    days: Optional[int] = None,
    claimed_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    keys = await identity_keys(db, user_id)
    if not owns_claimed_id(keys, claimed_user_id):
        raise PermissionError("Cannot read another user's mood logs")
    window = days or get_settings().MOOD_LOG_LOOKBACK_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(window)))
    cursor = (
        db[MOODS]
        .find({**identity_query(keys), "logged_at": {"$gte": cutoff}})
        .sort("logged_at", -1)
        .limit(90)
    )
    docs = await cursor.to_list(length=90)
    return [public_mood(doc) for doc in docs]

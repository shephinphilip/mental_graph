"""
services/context.py — MongoDB Context Aggregator
=================================================

Fetches three categories of historical data from MongoDB and assembles
them into a formatted string dictionary that is injected into the LLM
system prompt:

1. **User Memory** (``users`` collection)
   A narrative summary and bullet-point key takeaways from past sessions,
   stored by the platform's memory management pipeline.

2. **Recent Mood Logs** (``mood_logs`` collection)
   Mood check-in entries from the past N days (configurable via
   ``MOOD_LOG_LOOKBACK_DAYS`` in settings).  Gives the LLM a sense of
   the user's emotional trajectory over time.

3. **Active Habits** (``habit_events`` collection)
   Habits the user is currently tracking (``status == "active"``), with
   frequency and streak information.  Allows the LLM to reference and
   reinforce ongoing commitments.

The public function ``fetch_user_context()`` calls all three helpers and
returns a single dict that is consumed by both the LangGraph pipeline
(``services/graph.py``) and the SSE streaming pipeline
(``services/streaming.py``).

Error handling strategy
-----------------------
Each helper function may silently return a fallback string (e.g.
``"No mood logs recorded recently."```) if the relevant collection is
empty.  Database errors at this level are not suppressed — they propagate
up to ``fetch_user_context()`` callers, which should handle them.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings

logger = logging.getLogger(__name__)


async def fetch_user_context(db: AsyncIOMotorDatabase, user_id: str) -> Dict[str, Any]:
    """
    Aggregate cross-application context for a single user.

    Calls three MongoDB helpers in sequence and packages the results into
    a dict with three formatted string values, ready for string-formatting
    into the LLM system prompt.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The Motor async database handle (injected by the route layer).
    user_id : str
        The unique user identifier used to query all collections.

    Returns
    -------
    dict
        A dictionary with the following keys:
        - ``"user_memory"``   : str — summarised key takeaways from past sessions
        - ``"recent_moods"``  : str — formatted mood log entries for the past N days
        - ``"active_habits"`` : str — bullet list of currently active habits

    Raises
    ------
    motor.motor_asyncio.AsyncIOMotorCollection errors
        If MongoDB raises a connection or query error, it propagates
        to the caller (``fetch_context_node`` in ``graph.py`` or
        ``stream_chat_graph`` in ``streaming.py``), which log and handle it.
    """
    settings = get_settings()

    # Fetch all three context dimensions concurrently would be possible with
    # asyncio.gather, but sequential calls are used here for simplicity and
    # to keep MongoDB connection pool pressure low.
    user_memory = await _fetch_user_memory(db, user_id)
    recent_moods = await _fetch_recent_moods(db, user_id, settings.MOOD_LOG_LOOKBACK_DAYS)
    active_habits = await _fetch_active_habits(db, user_id)

    logger.debug(
        "Context aggregated for user=%s — memory=%d chars, moods=%d chars, habits=%d chars",
        user_id,
        len(user_memory),
        len(recent_moods),
        len(active_habits),
    )

    return {
        "user_memory": user_memory,
        "recent_moods": recent_moods,
        "active_habits": active_habits,
    }


# ── Private Helpers ───────────────────────────────────────────────────────────


async def _fetch_user_memory(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """
    Load the user's narrative memory summary and key takeaways.

    Queries the ``users`` collection for a single document matching
    ``user_id``.  Returns a formatted multi-line string, or a fallback
    message if the user has no stored memory yet.

    The ``users`` document is expected to have the optional fields:
    - ``memory_summary`` : str  — a short paragraph summarising the user's
      journey so far (written by the memory management pipeline)
    - ``key_takeaways``  : list[str] or str — bullet points from past sessions

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The Motor async database handle.
    user_id : str
        The unique user identifier.

    Returns
    -------
    str
        Formatted memory string ready for LLM prompt injection.
        Returns ``"No prior session history available."`` if the user
        document does not exist or has no memory fields set.

    Raises
    ------
    motor errors
        Propagated to the caller if the MongoDB query fails.
    """
    # Only fetch the fields we need — avoids pulling large documents
    user_doc = await db["users"].find_one(
        {"user_id": user_id},
        {"memory_summary": 1, "key_takeaways": 1},
    )

    # No user document yet (first-time user or new session)
    if not user_doc:
        return "No prior session history available."

    parts: List[str] = []

    # Append the narrative summary paragraph if it exists
    if summary := user_doc.get("memory_summary"):
        parts.append(summary)

    # Append key takeaways as bullet points
    if takeaways := user_doc.get("key_takeaways"):
        if isinstance(takeaways, list):
            # Each takeaway becomes a bullet point
            parts.extend(f"• {t}" for t in takeaways)
        else:
            # Scalar string (legacy format) — append as-is
            parts.append(str(takeaways))

    return "\n".join(parts) if parts else "No prior session history available."


async def _fetch_recent_moods(
    db: AsyncIOMotorDatabase, user_id: str, lookback_days: int
) -> str:
    """
    Fetch and format mood log entries from the past ``lookback_days`` days.

    Queries the ``mood_logs`` collection for entries newer than
    ``now - lookback_days``, sorted newest-first.  Each entry is
    formatted as a single line:

        ``Jan 15: anxious (3/10) — "Couldn't focus all day"``

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The Motor async database handle.
    user_id : str
        The unique user identifier.
    lookback_days : int
        Number of days to look back (controlled by ``MOOD_LOG_LOOKBACK_DAYS``
        in settings; default 7).

    Returns
    -------
    str
        Newline-separated mood log entries, or
        ``"No mood logs recorded recently."`` if none exist.

    Raises
    ------
    motor errors
        Propagated to the caller if the MongoDB query fails.
    """
    # Calculate the lookback cutoff timestamp in UTC
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    # Query: all mood logs for this user within the lookback window, newest first
    cursor = db["mood_logs"].find(
        {"user_id": user_id, "logged_at": {"$gte": cutoff}},
        {"mood": 1, "score": 1, "note": 1, "logged_at": 1},
    ).sort("logged_at", -1)

    entries: List[str] = []
    async for doc in cursor:
        # Format the timestamp; handle both datetime objects and raw strings
        logged_at = doc.get("logged_at", "")
        date_str = (
            logged_at.strftime("%b %d")
            if hasattr(logged_at, "strftime")
            else "unknown date"
        )

        mood = doc.get("mood", "—")
        score = doc.get("score", "")
        note = doc.get("note", "")

        # Build the entry string incrementally
        entry = f"{date_str}: {mood}"
        if score:
            entry += f" ({score}/10)"
        if note:
            entry += f' — "{note}"'

        entries.append(entry)

    return "\n".join(entries) if entries else "No mood logs recorded recently."


async def _fetch_active_habits(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """
    Return a bullet list of the user's currently active habits.

    Queries the ``habit_events`` collection for documents where
    ``user_id`` matches and ``status == "active"``.

    Each habit is formatted as:

        ``• Morning Journal (daily) — 12-day streak``

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The Motor async database handle.
    user_id : str
        The unique user identifier.

    Returns
    -------
    str
        Newline-separated bullet list of active habits, or
        ``"No active habits tracked."`` if the user has none.

    Raises
    ------
    motor errors
        Propagated to the caller if the MongoDB query fails.
    """
    # Filter to active habits only; project only the fields we format
    cursor = db["habit_events"].find(
        {"user_id": user_id, "status": "active"},
        {"title": 1, "frequency": 1, "streak": 1},
    )

    habits: List[str] = []
    async for doc in cursor:
        title = doc.get("title", "Untitled habit")
        freq = doc.get("frequency", "")
        streak = doc.get("streak", 0)

        # Build the habit line; frequency and streak are optional extras
        line = f"• {title}"
        if freq:
            line += f" ({freq})"
        if streak:
            line += f" — {streak}-day streak"

        habits.append(line)

    return "\n".join(habits) if habits else "No active habits tracked."

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
from services.marks import academic_context_for_turn
from services.users import get_by_identifier

logger = logging.getLogger(__name__)


async def fetch_user_context(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str = "",
    user_message: str = "",
    opening_turn: bool = False,
) -> Dict[str, Any]:
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
    session_id : str, optional
        Current session — used to load a *previous* session's transcript.
    user_message : str, optional
        Current turn text. Marks are fetched only for academic-looking turns.
    opening_turn : bool
        True when Zenark is opening a new session (skip marks injection).

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
    profile_fields = await _fetch_profile_and_structured_context(db, user_id)
    last_session = await _fetch_last_session_context(db, user_id, session_id)

    marks_block = await academic_context_for_turn(
        db, user_id, user_message, opening_turn=opening_turn
    )
    if marks_block:
        profile_fields["academic_context"] = marks_block

    pattern_context = "No longitudinal user patterns available for this turn."
    try:
        from services.patterns import get_pattern_context

        pattern_context = await get_pattern_context(db, user_id, user_message)
    except Exception:
        logger.exception("Pattern context fetch failed for user=%s", user_id)

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
        "last_session_context": last_session,
        "pattern_context": pattern_context,
        **profile_fields,
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
    user_doc = await get_by_identifier(db, user_id)

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


def _stringify_structured_block(value: Any, empty_message: str) -> str:
    """Turn optional stored academic/attendance/assessment payloads into prompt text."""
    if value is None or value == "" or value == [] or value == {}:
        return empty_message
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        lines = [f"• {item}" for item in value if item]
        return "\n".join(lines) if lines else empty_message
    if isinstance(value, dict):
        lines = [f"• {key}: {val}" for key, val in value.items() if val not in (None, "", [], {})]
        return "\n".join(lines) if lines else empty_message
    return str(value)


async def _fetch_profile_and_structured_context(
    db: AsyncIOMotorDatabase, user_id: str
) -> Dict[str, str]:
    """
    Load age/setting plus optional academic, attendance, and assessment summaries.

    Missing collections or fields must yield the explicit empty sentences the
    system prompt checks for, so the model does not invent stored marks.
    """
    user_doc = await get_by_identifier(db, user_id)

    if not user_doc:
        return {
            "user_profile": (
                "Age and setting unknown. Do not assume an age band. Let their language lead."
            ),
            "academic_context": "No academic data available",
            "attendance_context": "No attendance data available",
            "assessment_context": "No assessment data available",
        }

    profile_parts: List[str] = []
    if user_doc.get("age") is not None:
        profile_parts.append(f"Age: {user_doc['age']}")
    if user_doc.get("name"):
        profile_parts.append(f"Name: {user_doc['name']}")
    if user_doc.get("age_band"):
        profile_parts.append(f"Age band: {user_doc['age_band']}")
    class_label = user_doc.get("class") or user_doc.get("grade") or user_doc.get("class_level")
    if class_label:
        profile_parts.append(f"Class: {class_label}")
    if user_doc.get("school"):
        profile_parts.append(f"School: {user_doc['school']}")
    if user_doc.get("school_board") or user_doc.get("board"):
        profile_parts.append(f"Board: {user_doc.get('school_board') or user_doc.get('board')}")
    if user_doc.get("preferred_language"):
        profile_parts.append(f"Preferred language: {user_doc['preferred_language']}")
    if user_doc.get("chief_concern"):
        profile_parts.append(f"Chief concern on file: {user_doc['chief_concern']}")
    if user_doc.get("city"):
        profile_parts.append(f"City: {user_doc['city']}")
    if user_doc.get("tools_used"):
        profile_parts.append(
            _stringify_structured_block(user_doc.get("tools_used"), "")
        )

    academic = user_doc.get("academic_summary") or user_doc.get("academic_data")
    attendance = user_doc.get("attendance_summary") or user_doc.get("attendance_data")
    assessment = user_doc.get("assessment_summary") or user_doc.get("gds_summary")

    return {
        "user_profile": (
            "\n".join(p for p in profile_parts if p)
            or "Age and setting unknown. Do not assume an age band. Let their language lead."
        ),
        "academic_context": _stringify_structured_block(academic, "No academic data available"),
        "attendance_context": _stringify_structured_block(
            attendance, "No attendance data available"
        ),
        "assessment_context": _stringify_structured_block(
            assessment, "No assessment data available"
        ),
    }


async def _fetch_last_session_context(
    db: AsyncIOMotorDatabase, user_id: str, current_session_id: str
) -> str:
    """Load a compact transcript from the user's most recent *other* session."""
    query: Dict[str, Any] = {"user_id": user_id}
    if current_session_id:
        query["session_id"] = {"$ne": current_session_id}

    latest = await db["messages"].find_one(query, sort=[("created_at", -1)])
    if not latest:
        return "No previous session. This is the first conversation on file."

    prior_session_id = latest.get("session_id")
    cursor = (
        db["messages"]
        .find(
            {"user_id": user_id, "session_id": prior_session_id},
            {"role": 1, "content": 1, "created_at": 1},
        )
        .sort("created_at", -1)
        .limit(8)
    )
    docs = await cursor.to_list(length=8)
    docs.reverse()

    from services.security import decrypt_payload

    lines = []
    for doc in docs:
        role = "Student" if doc.get("role") == "user" else "Zenark"
        content = decrypt_payload(doc.get("content", ""))
        snippet = content.replace("\n", " ").strip()[:180]
        lines.append(f"{role}: {snippet}")

    when = latest.get("created_at")
    when_str = when.strftime("%d %b %Y") if hasattr(when, "strftime") else ""
    header = f"Previous session {prior_session_id}"
    if when_str:
        header += f" (last activity {when_str})"
    return (
        header
        + ". Refer to this naturally if useful; do not recap unless it matters.\n"
        + "\n".join(lines)
    )

"""
services/session_resume.py — Sub-500ms Session Resumption Service
==================================================================

Provides instant chat UI hydration when a user opens the app or resumes
a dropped session.  The entire handler is designed to complete in under
500 milliseconds by relying on MongoDB index-backed queries.

What this service does
----------------------
1. **Resolves the session ID** — if not explicitly provided, auto-detects
   the user's most recent session by querying the ``messages`` collection
   (requires an index on ``{user_id: 1, created_at: -1}``).

2. **Loads recent message history** — fetches the last 10 messages for the
   session (newest first, then reversed to chronological order) for
   immediate rendering in the chat UI.

3. **Computes dropped session context** — inspects the last message:
   - If the user sent the last message (no AI reply yet), it surfaces a
     "User left mid-thought" string so the AI can acknowledge it naturally.
   - If the last message was the AI's reply, it summarises where they left
     off.

4. **Fetches the active emotional state** — reads ``active_emotional_state``
   from the ``users`` collection to pre-populate a mood indicator in the UI.

5. **Decrypts message content** — calls ``decrypt_payload()`` on each
   message so the UI receives readable text rather than Fernet tokens.

Performance notes
-----------------
- All queries are single-document lookups or bounded range queries with
  ``LIMIT 10``, making the P99 latency well under 500 ms on indexed
  collections with a local or low-latency MongoDB cluster.
- The ``messages`` collection should have a compound index:
  ``{ session_id: 1, user_id: 1, created_at: -1 }``
- The ``users`` collection should have an index on ``{ user_id: 1 }``
"""

import logging
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from schemas import SessionResumeResponse
from services.chat_history import decrypt_message_doc, load_session_messages

logger = logging.getLogger(__name__)


async def resume_user_session(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: Optional[str] = None,
) -> SessionResumeResponse:
    """
    Rapidly restore session state for a returning user (< 500 ms SLA).

    Executes the four-step session restoration flow described in the module
    docstring and returns a fully populated ``SessionResumeResponse`` ready
    for immediate serialisation by the FastAPI route handler.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        Motor async database handle.
    user_id : str
        The unique user identifier.
    session_id : str, optional
        The specific session to resume.  If ``None`` or empty, the most
        recent session for ``user_id`` is auto-detected from the
        ``messages`` collection.

    Returns
    -------
    SessionResumeResponse
        Fully populated response with:
        - ``user_id`` and ``session_id`` (resolved)
        - ``is_resumed = True``
        - ``last_message_timestamp`` — ISO-8601 timestamp of the last message
        - ``dropped_session_context`` — human-readable continuation hint
        - ``recent_messages`` — list of last 10 messages (oldest first)
        - ``active_emotional_state`` — user's last recorded emotional state

    Raises
    ------
    motor.motor_asyncio errors
        If any MongoDB query fails (e.g. connection error, write concern
        timeout).  The FastAPI route handler maps these to HTTP 500.

    Notes
    -----
    This function always returns a valid ``SessionResumeResponse``.  Even
    for first-time users with no messages, the response will have an empty
    ``recent_messages`` list and ``is_resumed=True`` with a ``"default_session"``
    session ID.
    """
    settings = get_settings()  # noqa: F841 — available for future use (e.g. message limit)

    # ── Step 1: Resolve the Session ID ────────────────────────────────────────
    if not session_id:
        # Find the most recent message for this user to determine the last active session.
        # This requires an index on { user_id: 1, created_at: -1 } for performance.
        latest_msg = await db["messages"].find_one(
            {"user_id": user_id},
            sort=[("created_at", -1)],           # Get the newest message first
            projection={"session_id": 1},         # Only retrieve session_id to minimise data transfer
        )
        # Fall back to "default_session" for brand-new users with no messages
        session_id = latest_msg["session_id"] if latest_msg else "default_session"
        logger.debug(
            "Auto-detected session_id=%s for user=%s",
            session_id,
            user_id,
        )

    # ── Step 2: Fetch Recent Message History ──────────────────────────────────
    docs = await load_session_messages(db, user_id, session_id, limit=10)

    # ── Step 3: Build the Message List and Compute Dropped Context ────────────
    recent_messages: List[Dict[str, Any]] = []
    last_timestamp: Optional[str] = None
    dropped_context: Optional[str] = None

    for doc in docs:
        decrypted = decrypt_message_doc(doc)
        content = decrypted["content"]

        created_at = doc.get("created_at", "")
        created_at_str = (
            created_at.isoformat()
            if hasattr(created_at, "isoformat")
            else str(created_at)
        )

        recent_messages.append(
            {
                "role": decrypted["role"],
                "content": content,
                "timestamp": created_at_str,
            }
        )
        last_timestamp = created_at_str

    # ── Step 3b: Determine Dropped Session Context ────────────────────────────
    # The dropped context gives the AI a natural re-entry point when the
    # conversation resumes — it knows whether to continue from the user's
    # last thought or from the AI's last response.
    if recent_messages:
        last_msg = recent_messages[-1]  # The most recent message (chronologically)
        if last_msg["role"] == "user":
            # The user sent a message but never received a reply — common when
            # the app crashes, loses connection, or the session times out.
            dropped_context = (
                f"User left mid-thought: '{last_msg['content'][:100]}...'"
            )
        else:
            # The AI replied and then the session was closed normally.
            dropped_context = (
                f"Resuming after last AI response: '{last_msg['content'][:100]}...'"
            )

    # ── Step 4: Fetch Active Emotional State ──────────────────────────────────
    # The ``active_emotional_state`` field is written by the mood tracking
    # pipeline whenever the user logs a mood or the extraction pipeline
    # updates it.  It is used to pre-populate the UI's mood indicator badge.
    user_doc = await db["users"].find_one(
        {"user_id": user_id},
        {"active_emotional_state": 1},  # Project only this field for speed
    )
    emotional_state = user_doc.get("active_emotional_state") if user_doc else None

    logger.info(
        "Session resumed — user=%s session=%s — %d messages loaded, "
        "emotional_state=%s",
        user_id,
        session_id,
        len(recent_messages),
        emotional_state,
    )

    # Build and return the fully populated response payload
    return SessionResumeResponse(
        user_id=user_id,
        session_id=session_id,
        is_resumed=True,
        last_message_timestamp=last_timestamp,
        dropped_session_context=dropped_context,
        recent_messages=recent_messages,
        active_emotional_state=emotional_state,
    )

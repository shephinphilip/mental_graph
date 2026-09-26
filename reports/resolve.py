"""Mark an open event settled only when the person confirms that event."""

from __future__ import annotations

import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

_AFFIRM = re.compile(
    r"^(yes|yeah|yep|yup|haan|han|ha|ok|okay|done|sorted|resolved|it(?:'s| is) (?:done|sorted|resolved|over)|"
    r"i(?:'m| am) over it|yes it is|yes that(?:'s| is) done)\b",
    re.IGNORECASE,
)
_EXPLICIT = re.compile(
    r"\b(resolved|it(?:'s| is) (?:done|sorted|over)|i(?:'m| am) over it|that(?:'s| is) settled|no longer)\b",
    re.IGNORECASE,
)


def _tokens(label: str) -> List[str]:
    return [word for word in re.findall(r"[A-Za-z]{4,}", label.lower())][:4]


def mentions(text: str, label: str) -> bool:
    lowered = (text or "").lower()
    tokens = _tokens(label)
    if not tokens:
        return label.lower() in lowered
    return any(token in lowered for token in tokens)


def is_affirmation(message: str) -> bool:
    text = re.sub(r"\s+", " ", (message or "").strip())
    if not text or len(text) > 80:
        return False
    return bool(_AFFIRM.match(text))


def says_resolved(message: str) -> bool:
    return bool(_EXPLICIT.search(message or ""))


async def maybe_resolve_events(
    db,
    user_id: str,
    user_message: str,
    prior_assistant: str,
    *,
    opening_turn: bool = False,
) -> List[str]:
    """
    Return event ids just marked settled.

    A bare yes settles only the single open event the previous reply named.
    Naming a different event, or saying yes with no event in view, changes nothing.
    """
    if opening_turn:
        return []
    from services.apm import contains_crisis_signal

    if contains_crisis_signal(user_message or ""):
        return []
    from reports.store import mark_event_resolved, open_events

    events = await open_events(db, user_id)
    if not events:
        return []
    named_in_reply = [event for event in events if mentions(prior_assistant, event["label"])]
    settled: List[str] = []
    for event in events:
        named_by_user = mentions(user_message, event["label"])
        if says_resolved(user_message) and (named_by_user or mentions(prior_assistant, event["label"])):
            if await mark_event_resolved(db, user_id, event["event_id"]):
                settled.append(event["event_id"])
            continue
        if (
            is_affirmation(user_message)
            and len(named_in_reply) == 1
            and named_in_reply[0]["event_id"] == event["event_id"]
        ):
            if await mark_event_resolved(db, user_id, event["event_id"]):
                settled.append(event["event_id"])
    if settled:
        logger.info("Resolved events user=%s count=%d", user_id, len(settled))
    return settled


async def prior_assistant_text(db, user_id: str, session_id: str) -> str:
    if not session_id:
        return ""
    try:
        doc = await db["messages"].find_one(
            {"user_id": user_id, "session_id": session_id, "role": "assistant"},
            sort=[("created_at", -1)],
        )
    except Exception:
        logger.exception("Could not load prior assistant turn for user=%s", user_id)
        return ""
    if not doc:
        return ""
    from services.security import decrypt_payload

    return decrypt_payload(doc.get("content") or "")

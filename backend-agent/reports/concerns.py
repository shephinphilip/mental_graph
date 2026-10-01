"""Concern labels taken from the user's own messages, stamped in their timezone.

Assistant text is not a source. Missing timezone does not invent an offset.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

_RULES = (
    (re.compile(r"\b(anxious|anxiety|worried|worry|nervous|stress(?:ed)?)\b", re.I), re.compile(r"\bexams?\b", re.I), "Exam anxiety"),
    (re.compile(r"\b(concentrat\w*|focus\w*)\b", re.I), None, "Difficulty concentrating"),
    (re.compile(r"\bdisappoint", re.I), re.compile(r"\bparents?\b", re.I), "Fear of disappointing parents"),
    (re.compile(r"\b(can(?:not|'t)\s+sleep|insomnia|sleep problems?|trouble sleeping)\b", re.I), None, "Sleep problems"),
    (re.compile(r"\b(academic|marks|grades|homework|worksheet|schoolwork)\b", re.I), re.compile(r"\b(stress|anxious|worried|behind|heavy)\b", re.I), "Academic stress"),
)


def labels_from_user_text(text: str) -> List[str]:
    """Short labels only when the user's own words carry the concern."""
    body = (text or "").strip()
    if not body:
        return []
    found: List[str] = []
    for primary, secondary, label in _RULES:
        if not primary.search(body):
            continue
        if secondary is not None and not secondary.search(body):
            continue
        if label not in found:
            found.append(label)
    if not found and re.search(r"\b(anxious|anxiety|worried|worry)\b", body, re.I):
        found.append("Anxiety")
    return found


def local_timestamp(moment: datetime, zone_name: str) -> str:
    """UTC instant rendered as YYYY-MM-DDTHH:MM:SS±HH:MM in the user's zone."""
    when = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    local = when.astimezone(ZoneInfo(zone_name))
    return local.isoformat(timespec="seconds")


def build_key_concerns(
    messages: List[Dict[str, Any]],
    zone_name: Optional[str],
) -> Dict[str, List[str]]:
    """Map each user message time to the concerns in that message.

    ``messages`` are stored chat rows. Only ``role == "user"`` is read.
    Without an IANA timezone the result is empty rather than a guessed offset.
    """
    if not zone_name:
        return {}
    grouped: Dict[str, List[str]] = {}
    for message in messages:
        if message.get("role") != "user":
            continue
        from backend_core.security import decrypt_payload

        text = decrypt_payload(str(message.get("content") or ""))
        if text == "WELCOME MESSAGE":
            continue
        labels = labels_from_user_text(text)
        if not labels:
            continue
        created = message.get("created_at")
        if not isinstance(created, datetime):
            continue
        stamp = local_timestamp(created, zone_name)
        bucket = grouped.setdefault(stamp, [])
        for label in labels:
            if label not in bucket:
                bucket.append(label)
    return grouped

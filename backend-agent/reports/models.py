"""Shape checks for a stored session reading. The model does not write Mongo."""

from __future__ import annotations

import re
import secrets
from typing import Any, Dict, List, Optional

from tasks.validate import validate_proposals

MAX_EVENTS = 5
MAX_LABEL = 140


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def clamp_metric(value: Any) -> Optional[int]:
    """Session load from 1 (settled) to 10 (heavy). Missing stays unset."""
    if value is None or value is False:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return max(1, min(10, int(round(number))))


def normalize_events(raw: Any) -> List[Dict[str, Any]]:
    """Major events only. A new reading starts unresolved unless already settled."""
    if not isinstance(raw, list):
        return []
    events: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            label = item.strip()
            resolved = False
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("event") or "").strip()
            resolved = bool(item.get("resolved"))
        else:
            continue
        label = re.sub(r"\s+", " ", label)[:MAX_LABEL].strip()
        key = _norm(label)
        if len(key) < 3 or key in seen:
            continue
        seen.add(key)
        events.append(
            {
                "event_id": f"evt_{secrets.token_hex(4)}",
                "label": label,
                "resolved": resolved,
            }
        )
        if len(events) >= MAX_EVENTS:
            break
    return events


def normalize_proposed_tasks(raw: Any) -> List[Dict[str, Any]]:
    """Concrete tasks the person may choose to add. Nothing is added yet."""
    proposals = validate_proposals(raw, [])
    tasks = []
    for item in proposals:
        tasks.append(
            {
                "id": f"proposal_{secrets.token_hex(4)}",
                "title": item["title"],
                "description": item["description"],
                "added": False,
            }
        )
    return tasks


def merge_events(
    fresh: List[Dict[str, Any]], previous: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """A settled event stays settled, even if a later reading drops or repeats it."""
    prior = {_norm(str(item.get("label") or "")): item for item in previous}
    merged: List[Dict[str, Any]] = []
    seen = set()
    for event in fresh:
        key = _norm(event["label"])
        old = prior.get(key)
        if old and old.get("resolved"):
            event = {
                **event,
                "event_id": old.get("event_id") or event["event_id"],
                "resolved": True,
                "resolved_at": old.get("resolved_at"),
            }
        merged.append(event)
        seen.add(key)
    for old in previous:
        key = _norm(str(old.get("label") or ""))
        if old.get("resolved") and key and key not in seen:
            merged.append(old)
            seen.add(key)
    return merged[:MAX_EVENTS]


def merge_tasks(
    fresh: List[Dict[str, Any]], previous: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    prior = {_norm(str(item.get("title") or "")): item for item in previous}
    merged = []
    for task in fresh:
        old = prior.get(_norm(task["title"]))
        if old:
            task = {
                **task,
                "id": old.get("id") or task["id"],
                "added": bool(old.get("added")),
            }
        merged.append(task)
    return merged

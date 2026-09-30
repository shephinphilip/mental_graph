"""Backend checks for report-proposed tasks. The model does not write Mongo."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from services.apm import contains_crisis_signal

MAX_TASKS = 3
MAX_TITLE = 80
MAX_DESCRIPTION = 240

_VAGUE = {
    "study harder",
    "work harder",
    "be better",
    "improve your mental health",
    "get better",
    "try harder",
}


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().lower()


def validate_proposals(
    raw: Any,
    existing: Sequence[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    Keep at most three concrete tasks.

    Extra items are dropped, not written. A title that matches any current
    task, including a completed or deleted one, is not added again.
    """
    if not isinstance(raw, list):
        return []
    taken = {_norm(str(item.get("title") or "")) for item in existing if item.get("title")}
    kept: List[Dict[str, str]] = []
    for item in raw:
        if len(kept) >= MAX_TASKS:
            break
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or "").strip()
        if not title or len(title) > MAX_TITLE:
            continue
        if len(description) > MAX_DESCRIPTION:
            continue
        if _norm(title) in _VAGUE:
            continue
        if contains_crisis_signal(title) or contains_crisis_signal(description):
            continue
        key = _norm(title)
        if key in taken:
            continue
        taken.add(key)
        kept.append({"title": title, "description": description})
    return kept

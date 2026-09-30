"""Shape checks for model-proposed facts."""

from __future__ import annotations

import re
from typing import Any, Dict, List

CATEGORIES = (
    "ACADEMIC",
    "FAMILY",
    "SOCIAL",
    "HEALTH",
    "SLEEP",
    "COPING",
    "GOAL",
    "PREFERENCE",
    "EVENT",
    "OTHER",
)

MAX_FACTS = 8
MAX_FACT_CHARS = 200

_DIAGNOSIS = re.compile(
    r"\b(diagnos|disorder|depressi(on|ve)|bipolar|schizo|adhd|ocd|ptsd|clinical)\w*",
    re.IGNORECASE,
)


def fact_key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", re.sub(r"\s+", " ", (text or "").strip().lower()))


def clamp_importance(value: Any, default: float = 0.5) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return max(0.05, min(1.0, number))


def normalize_facts(raw: Any) -> List[Dict[str, Any]]:
    """Keep short, durable, non-clinical statements. Quotes are not facts."""
    if not isinstance(raw, list):
        return []
    facts: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            item = {"fact": item}
        if not isinstance(item, dict):
            continue
        text = re.sub(r"\s+", " ", str(item.get("fact") or item.get("text") or "")).strip()
        text = text.strip('"\u201c\u201d')
        if len(text) < 8 or len(text) > MAX_FACT_CHARS:
            continue
        if _DIAGNOSIS.search(text):
            continue
        key = fact_key(text)
        if not key or key in seen:
            continue
        category = str(item.get("category") or "OTHER").strip().upper()
        if category not in CATEGORIES:
            category = "OTHER"
        seen.add(key)
        facts.append(
            {
                "fact": text,
                "key": key,
                "category": category,
                "importance": clamp_importance(item.get("importance")),
            }
        )
        if len(facts) >= MAX_FACTS:
            break
    return facts

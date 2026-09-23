"""Journal write rules. Mood stays the four values the product already uses."""

from __future__ import annotations

from typing import Iterable, List, Sequence

MOODS = ("😊", "😃", "😐", "😢")
NEGATIVE_MOODS = frozenset({"😢"})
POSITIVE_MOODS = frozenset({"😊", "😃"})

TOPIC_WORDS = (
    "exam",
    "test",
    "physics",
    "family",
    "friend",
    "sleep",
    "school",
    "doubt",
    "work",
    "lonely",
)


def clean_tags(tags: Iterable[str] | None) -> List[str]:
    cleaned = []
    for tag in tags or []:
        text = str(tag or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def topics_from(title: str, content: str, tags: Sequence[str]) -> List[str]:
    """Words used for pattern counts. The original entry is not rewritten."""
    haystack = f"{title} {content}".lower()
    found = [word for word in TOPIC_WORDS if word in haystack]
    for tag in tags:
        label = str(tag).strip().lstrip("#").lower()
        if label:
            found.append(label)
    return sorted(set(found))


def validate_entry(*, title: str, content: str, mood: str, time_spent: int) -> None:
    if not isinstance(title, str) or len(title.strip()) < 3:
        raise ValueError("title must be at least 3 characters")
    if not isinstance(content, str) or len(content.strip()) < 10:
        raise ValueError("content must be at least 10 characters")
    if mood not in MOODS:
        raise ValueError("mood must be one of the four journal moods")
    if time_spent < 0:
        raise ValueError("time_spent cannot be negative")

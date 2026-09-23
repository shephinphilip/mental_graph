"""Bounded journal preview for prompts. Not the full collection."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

from config import get_settings
from journaling.service import recent_entries
from services.apm import contains_crisis_signal

logger = logging.getLogger(__name__)

EMPTY_JOURNAL_CONTEXT = "No journal entries available."

_RELEVANCE = (
    "Use a journal note only when this message continues the same concern. "
    "Do not say you searched a database. Do not recite an old entry just to "
    "show memory. The emoji is what they selected. Do not replace it with a "
    "guessed feeling, and do not say a journal mood was caused by anything else."
)


def _preview(text: str, limit: int) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= limit:
        return body
    return body[: max(0, limit - 1)].rstrip() + "…"


def format_journal_context(entries: List[Dict[str, Any]]) -> str:
    settings = get_settings()
    usable = []
    for entry in entries:
        if contains_crisis_signal(str(entry.get("content") or "")) or contains_crisis_signal(
            str(entry.get("title") or "")
        ):
            continue
        usable.append(entry)
    usable = usable[: settings.JOURNAL_CONTEXT_LIMIT]
    if not usable:
        return EMPTY_JOURNAL_CONTEXT
    lines = [
        "RECENT JOURNAL CONTEXT",
        "The mood emoji is what the person selected. It is not an inferred emotion.",
        "",
        "Recent entries:",
    ]
    for entry in usable:
        stamp = entry.get("timestamp")
        if isinstance(stamp, datetime):
            day = stamp.date().isoformat()
        else:
            day = "unknown date"
        preview = _preview(str(entry.get("content") or ""), settings.JOURNAL_PREVIEW_CHARS)
        lines.extend(
            [
                f"- {day}",
                f"  Mood: {entry.get('mood')}",
                f"  Title: {entry.get('title')}",
                f'  Summary/content preview: "{preview}"',
            ]
        )
    lines.extend(["", "Relevance:", _RELEVANCE])
    text = "\n".join(lines)
    if len(text) > settings.JOURNAL_CONTEXT_MAX_CHARS:
        text = text[: settings.JOURNAL_CONTEXT_MAX_CHARS].rstrip() + "…"
    if "_id" in text or "ObjectId" in text:
        raise RuntimeError("journal context leaked storage metadata")
    return text


async def build_journal_context(db, user_id: str) -> str:
    try:
        settings = get_settings()
        rows = await recent_entries(db, user_id, limit=settings.JOURNAL_CONTEXT_LIMIT)
        return format_journal_context(rows)
    except Exception:
        logger.exception("Journal context failed for user=%s", user_id)
        return EMPTY_JOURNAL_CONTEXT

"""Response shaping only. No queries, no LLM calls."""

from __future__ import annotations


def public_sleep(doc: dict) -> dict:
    return {
        "user_id": doc.get("user_id"),
        "bedtime": doc.get("bedtime"),
        "wake_up_time": doc.get("wake_up_time"),
        "total_duration_minutes": doc.get("total_duration_minutes"),
        "date": doc.get("date"),
        "created_at": doc.get("created_at"),
    }


def public_journal(doc: dict, *, preview: bool = False) -> dict:
    from config import get_settings
    from journaling.service import public_entry

    limit = get_settings().JOURNAL_PREVIEW_CHARS if preview else None
    payload = public_entry(doc, preview_chars=limit)
    if doc.get("duplicate"):
        payload["duplicate"] = True
    return payload


def public_execution(doc: dict) -> dict:
    return {
        "execution_id": doc.get("execution_id"),
        "meditation_id": doc.get("meditation_id"),
        "status": doc.get("status"),
        "execution_nonce": doc.get("execution_nonce"),
        "user_helpfulness_feedback": doc.get("user_helpfulness_feedback"),
        "listen_duration_seconds": doc.get("listen_duration_seconds") or 0,
    }

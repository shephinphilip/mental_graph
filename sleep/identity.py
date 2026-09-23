"""Authenticated identity aliases for sleep_logs. Never a client-chosen user."""

from __future__ import annotations

from typing import Any, Dict, List

from services.users import get_by_identifier


def _add(keys: List[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in keys:
        keys.append(text)


async def identity_keys(db, authenticated_user_id: str) -> List[str]:
    """
    Values that may appear in sleep_logs.user_id for this person.

    The token subject is always included. Email and stored user_id are added
    only from that person's own user document.
    """
    keys: List[str] = []
    _add(keys, authenticated_user_id)
    user = await get_by_identifier(db, authenticated_user_id)
    if isinstance(user, dict):
        _add(keys, user.get("user_id"))
        _add(keys, user.get("email"))
        if user.get("_id") is not None:
            _add(keys, user.get("_id"))
    return keys


def owns_claimed_id(keys: List[str], claimed_user_id: Any) -> bool:
    if claimed_user_id is None or str(claimed_user_id).strip() == "":
        return True
    return str(claimed_user_id).strip() in keys


def identity_query(keys: List[str]) -> Dict[str, Any]:
    return {"user_id": {"$in": keys or ["__no_such_user__"]}}

"""Cursor and offset pagination over an already loaded page of school rows."""

from __future__ import annotations

import base64
from typing import Callable, Optional


def encode_cursor(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str) -> str:
    try:
        padded = value + "=" * (-len(value) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise ValueError("Invalid cursor") from exc
    if not raw or len(raw) > 200:
        raise ValueError("Invalid cursor")
    return raw


def paginate(
    items: list,
    *,
    limit: int,
    page: int,
    cursor: Optional[str],
    key: Callable,
    reverse: bool = False,
):
    ordered = sorted(items, key=lambda item: str(key(item)), reverse=reverse)
    total = len(ordered)
    page_no = page
    if cursor:
        if reverse:
            ordered = [item for item in ordered if str(key(item)) < cursor]
        else:
            ordered = [item for item in ordered if str(key(item)) > cursor]
        page_no = 1
    else:
        start = (page - 1) * limit
        ordered = ordered[start:]
    chunk = ordered[:limit]
    has_next = len(ordered) > limit
    next_cursor = encode_cursor(str(key(chunk[-1]))) if has_next and chunk else None
    return chunk, {
        "page": page_no,
        "limit": limit,
        "has_next": has_next,
        "next_cursor": next_cursor,
        "total": total,
    }

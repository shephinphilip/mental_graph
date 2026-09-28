"""JSON helpers for dashboard responses. Datetimes leave as UTC timestamps."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.request_id import current_request_id


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, float) and value != value:
        return None
    return value


def ok(data: Any, **meta: Any) -> dict:
    body = {"request_id": current_request_id() or ""}
    body.update(meta)
    return {"success": True, "data": json_safe(data), "meta": json_safe(body)}

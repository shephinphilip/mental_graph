"""Publisher entry points. Payloads stay on the authorized school key."""

from __future__ import annotations

from typing import Any

from dashboard.events import bus


def publish(school_key: str, event: str, **fields: Any) -> int:
    return bus.publish(school_key, event, **fields)

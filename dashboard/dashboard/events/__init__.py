"""School-scoped server-sent events.

One process, one bus. A subscriber only receives events published with its
school key. There is no Redis in this deployment; a second API worker will
not see the other worker's queue. Dashboard writes publish here directly.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

EVENTS = frozenset(
    {
        "student_risk_changed",
        "attendance_updated",
        "academic_score_updated",
        "wellbeing_updated",
        "class_metric_updated",
        "school_metric_updated",
        "report_ready",
        "notification_created",
        "intervention_updated",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class SchoolEventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, school_key: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.setdefault(school_key, []).append(queue)
        return queue

    def unsubscribe(self, school_key: str, queue: asyncio.Queue) -> None:
        rows = self._subs.get(school_key) or []
        if queue in rows:
            rows.remove(queue)
        if not rows and school_key in self._subs:
            self._subs.pop(school_key, None)

    def publish(self, school_key: str, event: str, **fields: Any) -> int:
        if event not in EVENTS:
            raise ValueError(f"Unknown dashboard event {event}")
        payload = {
            "event": event,
            "school_id": school_key,
            "timestamp": utc_now(),
        }
        for key, value in fields.items():
            if key in {"event", "school_id"}:
                continue
            payload[key] = value
        delivered = 0
        for queue in list(self._subs.get(school_key) or []):
            try:
                queue.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                continue
        return delivered

    def reset(self) -> None:
        self._subs.clear()


bus = SchoolEventBus()

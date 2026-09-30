"""Safe structured logging and in-process counters for proactive decisions."""

from __future__ import annotations

import time
from typing import Dict, Optional

from config.config import logger
from core.logging import hash_user_id
from core.request_id import current_request_id
from services.telemetry import register_metric

_COUNTS: Dict[str, int] = {
    "candidates_evaluated": 0,
    "candidates_suppressed": 0,
    "questions_approved": 0,
    "questions_dispatched": 0,
    "questions_responded": 0,
    "questions_expired": 0,
    "duplicate_attempts_suppressed": 0,
    "validation_rejections": 0,
    "safety_suppression_count": 0,
}


def bump(name: str, amount: int = 1) -> None:
    if name not in _COUNTS:
        return
    _COUNTS[name] += amount


def snapshot() -> Dict[str, int]:
    return dict(_COUNTS)


def reset_for_tests() -> None:
    for key in _COUNTS:
        _COUNTS[key] = 0


def log_decision(
    *,
    user_id: str,
    decision: str,
    trigger_type: str = "",
    suppression_reason: str = "",
    event_id: str = "",
    status: str = "",
    latency_ms: float = 0.0,
) -> None:
    register_metric("proactive_decision")
    if trigger_type:
        register_metric("proactive_trigger_type")
    if suppression_reason:
        register_metric("proactive_suppression")
    if status:
        register_metric("proactive_status")
    logger.info(
        "proactive request_id=%s user=%s decision=%s trigger=%s "
        "suppression=%s event_id=%s status=%s latency_ms=%.1f",
        current_request_id() or "-",
        hash_user_id(user_id),
        decision,
        trigger_type or "-",
        suppression_reason or "-",
        event_id or "-",
        status or "-",
        latency_ms,
    )


class Timer:
    def __init__(self) -> None:
        self._started = time.perf_counter()

    def ms(self) -> float:
        return (time.perf_counter() - self._started) * 1000.0

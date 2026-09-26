"""Structured access logging. Never logs tokens, passwords, or message bodies."""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger("zenark.access")


def hash_user_id(user_id: Optional[str]) -> str:
    if not user_id:
        return ""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )


def log_request(
    *,
    request_id: str,
    method: str,
    route: str,
    status_code: int,
    latency_ms: float,
    user_id: Optional[str] = None,
) -> None:
    logger.info(
        "request_id=%s method=%s route=%s status=%s latency_ms=%.1f user=%s",
        request_id,
        method,
        route,
        status_code,
        latency_ms,
        hash_user_id(user_id),
    )

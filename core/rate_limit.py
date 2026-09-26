"""Process-local sliding-window limiter.

This is enough for a single API process. Multiple replicas each keep their
own counters, so production horizontal scaling needs a shared store
(``RATE_LIMIT_BACKEND=redis`` when a Redis URL is configured). Until then
the limiter still protects a single worker from a noisy client.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request

from config import get_settings

_windows: Dict[str, Deque[float]] = defaultdict(deque)
_lock = Lock()

_SKIP_PREFIXES = ("/health", "/docs", "/redoc", "/openapi.json")


def _limit_for(path: str) -> Tuple[int, int]:
    settings = get_settings()
    if path.endswith("/auth/login") or path.endswith("/auth/signup"):
        return settings.RATE_LIMIT_AUTH_PER_MINUTE, 60
    if path.endswith("/chat/stream"):
        return settings.RATE_LIMIT_STREAM_PER_MINUTE, 60
    if path.endswith("/chat/send") or path.endswith("/chat/welcome"):
        return settings.RATE_LIMIT_CHAT_PER_MINUTE, 60
    if path.endswith("/session/report"):
        return settings.RATE_LIMIT_REPORT_PER_MINUTE, 60
    return settings.RATE_LIMIT_DEFAULT_PER_MINUTE, 60


def allow(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        bucket = _windows[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _skipped(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _SKIP_PREFIXES)


async def enforce_rate_limit(request: Request) -> None:
    settings = get_settings()
    if not settings.RATE_LIMIT_ENABLED:
        return
    if _skipped(request.url.path):
        return
    limit, window = _limit_for(request.url.path)
    if limit <= 0:
        return
    identity = request.headers.get("authorization") or (request.client.host if request.client else "anon")
    key = f"{request.url.path}:{identity[-32:]}"
    if not allow(key, limit, window):
        raise HTTPException(status_code=429, detail="Too many requests.")

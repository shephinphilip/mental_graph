"""Timeout, retry, concurrency, and a process-local circuit breaker for LLM calls.

Only transient failures are retried. A retry never writes to Mongo — callers
must persist user actions before invoking the model.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

_TRANSIENT = (
    TimeoutError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)

_semaphore: asyncio.Semaphore | None = None
_failures = 0
_opened_at = 0.0


def _sema() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max(1, get_settings().LLM_CONCURRENCY))
    return _semaphore


def _circuit_open() -> bool:
    settings = get_settings()
    if _failures < settings.LLM_CIRCUIT_FAILURES:
        return False
    return (time.monotonic() - _opened_at) < settings.LLM_CIRCUIT_RESET_SECONDS


def _record_success() -> None:
    global _failures
    _failures = 0


def _record_failure() -> None:
    global _failures, _opened_at
    _failures += 1
    if _failures >= get_settings().LLM_CIRCUIT_FAILURES:
        _opened_at = time.monotonic()


def reset_circuit_for_tests() -> None:
    global _failures, _opened_at
    _failures = 0
    _opened_at = 0.0


async def resilient_ainvoke(llm: Any, messages: Any, **kwargs: Any):
    """Call ``llm.ainvoke`` with timeout, jittered backoff, and a concurrency cap."""
    settings = get_settings()
    hardened = settings.APP_ENV in {"production", "staging"}
    if hardened and _circuit_open():
        raise RuntimeError("llm_circuit_open")

    last_exc: Exception | None = None
    attempts = 1 + max(0, settings.LLM_MAX_RETRIES) if hardened else 1
    for attempt in range(attempts):
        try:
            async with _sema():
                result = await asyncio.wait_for(
                    llm.ainvoke(messages, **kwargs),
                    timeout=settings.LLM_TIMEOUT_SECONDS,
                )
            _record_success()
            return result
        except Exception as exc:
            last_exc = exc
            transient = isinstance(exc, _TRANSIENT) or "throttl" in str(exc).lower() or "timeout" in str(exc).lower()
            if not transient or attempt == attempts - 1:
                _record_failure()
                raise
            delay = settings.LLM_RETRY_BASE_SECONDS * (2 ** attempt)
            delay += random.uniform(0, delay * 0.25)
            logger.warning(
                "Transient LLM failure attempt=%s retrying_in=%.2fs reason=%s",
                attempt + 1,
                delay,
                type(exc).__name__,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc

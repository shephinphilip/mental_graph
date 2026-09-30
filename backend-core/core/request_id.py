"""Request-id helpers. Values are random hex — never user data."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

REQUEST_ID_HEADER = "X-Request-ID"
_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex


def current_request_id() -> str:
    return _request_id.get() or ""


def bind_request_id(value: str) -> None:
    _request_id.set(value)

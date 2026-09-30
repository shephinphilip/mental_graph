"""Shared HTTP dependencies and error mapping for the API layer.

These moved verbatim out of ``app.py`` during the Phase 2 API centralization.
Behavior is unchanged; the symbols simply have a home the route modules can
import without pulling in the whole application object.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from backend_core.users import verify_access_token
from config.config import logger


async def authenticated_user_id(
    authorization: Optional[str] = Header(default=None),
) -> str:
    """Resolve the authenticated user id from a Bearer token, or 401."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        return verify_access_token(authorization[7:].strip())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def assert_owner(claimed_user_id: str, authenticated_id: str) -> None:
    """403 unless a client-claimed id exactly matches the authenticated one.

    A client-supplied id is only ever *checked* here — it never selects a row.
    """
    if claimed_user_id != authenticated_id:
        logger.warning("Owner check failed")
        raise HTTPException(status_code=403, detail="User identity mismatch")


def task_http(exc: Exception) -> HTTPException:
    """Map the domain exception vocabulary to HTTP status codes."""
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="Task request failed")

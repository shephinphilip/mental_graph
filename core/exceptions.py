"""Consistent error envelopes. Internal details stay in logs, not responses."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from core.request_id import current_request_id

STATUS_CODES = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "NOT_READY",
}

SAFE_MESSAGES = {
    401: "Authentication required.",
    403: "You cannot access that resource.",
    404: "Not found.",
    409: "Conflict.",
    429: "Too many requests.",
    500: "An unexpected error occurred.",
    503: "Service is not ready.",
}


def error_body(status_code: int, message: str, request_id: Optional[str] = None) -> dict:
    return {
        "success": False,
        "error": {
            "code": STATUS_CODES.get(status_code, "ERROR"),
            "message": message,
            "request_id": request_id or current_request_id() or "",
        },
    }


def _client_message(status_code: int, detail) -> str:
    if status_code >= 500:
        return SAFE_MESSAGES[500]
    if isinstance(detail, str) and detail.strip():
        return detail
    return SAFE_MESSAGES.get(status_code, "Request failed.")


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "") or current_request_id()
    body = error_body(exc.status_code, _client_message(exc.status_code, exc.detail), request_id)
    # Existing clients and tests read ``detail``. Keep it as a sibling.
    body["detail"] = body["error"]["message"]
    headers = dict(exc.headers or {})
    if request_id:
        headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "") or current_request_id()
    body = error_body(400, "Invalid request.", request_id)
    body["detail"] = "Invalid request."
    return JSONResponse(status_code=400, content=body, headers={"X-Request-ID": request_id})


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "") or current_request_id()
    body = error_body(500, SAFE_MESSAGES[500], request_id)
    body["detail"] = SAFE_MESSAGES[500]
    return JSONResponse(status_code=500, content=body, headers={"X-Request-ID": request_id})

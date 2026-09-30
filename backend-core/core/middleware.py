"""Request-id, access log, and optional host/CORS guards."""

from __future__ import annotations

import time
from typing import Iterable, Optional

from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core.logging import log_request
from core.request_id import REQUEST_ID_HEADER, bind_request_id, new_request_id


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        incoming = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
        request_id = incoming if incoming and incoming.isalnum() and len(incoming) <= 64 else new_request_id()
        bind_request_id(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        user_id = getattr(request.state, "user_id", None)
        log_request(
            request_id=request_id,
            method=request.method,
            route=request.url.path,
            status_code=response.status_code,
            latency_ms=(time.perf_counter() - started) * 1000,
            user_id=user_id,
        )
        return response


def install_middleware(
    app: FastAPI,
    *,
    cors_origins: Optional[Iterable[str]] = None,
    trusted_hosts: Optional[Iterable[str]] = None,
) -> None:
    app.add_middleware(RequestContextMiddleware)
    origins = [item.strip() for item in (cors_origins or []) if item and item.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    hosts = [item.strip() for item in (trusted_hosts or []) if item and item.strip()]
    if hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(hosts))

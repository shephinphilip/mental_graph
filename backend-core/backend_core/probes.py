"""Process liveness for applications other than the historical product API.

The therapeutic API keeps its own ``/health`` payload in ``api.routes.health``.
These probes exist so each process can be checked on its own port.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.config import logger


def process_router(service: str) -> APIRouter:
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    async def liveness() -> dict:
        return {"status": "live", "service": service}

    @router.get("/health/ready")
    async def readiness(request: Request):
        client = getattr(request.app.state, "mongo_client", None)
        if client is None:
            logger.warning("Readiness failed reason=mongo_unconfigured service=%s", service)
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "mongo_unconfigured", "service": service},
            )
        try:
            await client.admin.command("ping")
        except Exception:
            logger.warning("Readiness failed reason=mongo_unavailable service=%s", service)
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reason": "mongo_unavailable", "service": service},
            )
        return {"status": "ready", "service": service}

    return router

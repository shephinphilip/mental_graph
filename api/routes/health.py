"""Liveness and readiness probes. Never expose secrets or connection strings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from config.config import logger

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check():
    """Compatibility probe. Same payload as before; not a dependency check."""
    return {
        "status": "healthy",
        "service": "therapeutic-ai-chatbot",
        "version": "0.4.0",
    }


@router.get("/health/live")
async def liveness():
    """Process is running. Orchestrators should restart only if this fails."""
    return {"status": "live"}


@router.get("/health/ready")
async def readiness(request: Request):
    """Mongo is reachable. LLM readiness is not required to accept traffic."""
    client = getattr(request.app.state, "mongo_client", None)
    if client is None:
        logger.warning("Readiness failed reason=mongo_unconfigured")
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "mongo_unconfigured"})
    try:
        await client.admin.command("ping")
    except Exception:
        logger.warning("Readiness failed reason=mongo_unavailable")
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "mongo_unavailable"})
    return {"status": "ready"}

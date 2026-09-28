"""Canonical FastAPI factory. ``app.py`` imports the instance this builds."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError

from api.router import build_api_router
from config.config import get_settings, logger
from core.exceptions import (
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from core.logging import configure_logging
from core.middleware import install_middleware
from core.rate_limit import enforce_rate_limit
from database import lifespan
from fastapi import HTTPException


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    logger.info("Zenark API starting env=%s log_level=%s", settings.APP_ENV, settings.LOG_LEVEL)
    application = FastAPI(
        title="Zenark API",
        description=(
            "Personalized AI therapeutic companion. Compatibility routes keep "
            "existing clients working; /api/v1 remounts the same handlers."
        ),
        version="0.4.0",
        lifespan=lifespan,
    )
    origins = [item.strip() for item in settings.CORS_ORIGINS.split(",") if item.strip()]
    hosts = [item.strip() for item in settings.TRUSTED_HOSTS.split(",") if item.strip()]
    install_middleware(application, cors_origins=origins, trusted_hosts=hosts)
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.include_router(build_api_router(), dependencies=[Depends(enforce_rate_limit)])
    from api.routes.voice import psychiatrist_voice

    # Nested include_router does not expose WebSocket routes in this FastAPI
    # version. Register the live sockets on the application object.
    application.add_api_websocket_route("/ws/psychiatrist-voice", psychiatrist_voice)
    application.add_api_websocket_route("/api/v1/ws/psychiatrist-voice", psychiatrist_voice)
    return application

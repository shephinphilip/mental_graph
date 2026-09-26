"""Canonical FastAPI factory. ``app.py`` imports the instance this builds."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError

from api.router import build_api_router
from config import get_settings
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
    return application

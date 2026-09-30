"""Dashboard application. It does not mount mental-health or Exam Buddy routes."""

from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from backend_core.probes import process_router
from backend_core.security import CryptoIntegrityError
from config.config import get_settings, logger
from core.exceptions import (
    crypto_integrity_exception_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_exception_handler,
)
from core.logging import configure_logging
from core.middleware import install_middleware
from core.rate_limit import enforce_rate_limit
from dashboard.indexes import ensure_dashboard_indexes
from dashboard.router import router as dashboard_router
from database import lifespan
from db.indexes import register_index_hook

register_index_hook(ensure_dashboard_indexes)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    from core.runtime_guard import assert_environment_secrets

    assert_environment_secrets(settings)
    logger.info("Zenark dashboard starting env=%s log_level=%s", settings.APP_ENV, settings.LOG_LEVEL)
    hardened = settings.APP_ENV.lower() in {"production", "prod", "staging", "preprod"}
    application = FastAPI(
        title="Zenark Dashboard",
        description="School and admin dashboard.",
        version="0.4.0",
        lifespan=lifespan,
        docs_url=None if hardened else "/docs",
        redoc_url=None if hardened else "/redoc",
        openapi_url=None if hardened else "/openapi.json",
    )
    origins = [item.strip() for item in settings.CORS_ORIGINS.split(",") if item.strip()]
    hosts = [item.strip() for item in settings.TRUSTED_HOSTS.split(",") if item.strip()]
    install_middleware(application, cors_origins=origins, trusted_hosts=hosts)
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(CryptoIntegrityError, crypto_integrity_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
    application.include_router(process_router("zenark-dashboard"))
    application.include_router(
        dashboard_router,
        prefix="/api/v1",
        dependencies=[Depends(enforce_rate_limit)],
    )
    return application


app = create_app()

"""Platform application. Account primitives live in this package.

Product routes for chat, Exam Buddy, and the school dashboard are not mounted
here. Login stays on the mental-health API so existing clients keep one host.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
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
from database import lifespan


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL)
    from core.runtime_guard import assert_environment_secrets

    assert_environment_secrets(settings)
    logger.info("Zenark core starting env=%s log_level=%s", settings.APP_ENV, settings.LOG_LEVEL)
    hardened = settings.APP_ENV.lower() in {"production", "prod", "staging", "preprod"}
    application = FastAPI(
        title="Zenark Core",
        description="Shared platform process.",
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
    application.include_router(process_router("zenark-core"))
    return application


app = create_app()

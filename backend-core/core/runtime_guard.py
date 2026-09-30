"""Refuse known-insecure defaults when APP_ENV is production or staging."""

from __future__ import annotations

import os

_PLACEHOLDER_ENCRYPTION = "gAAAAABl_secret_key_placeholder_32bytes_len="
_PLACEHOLDER_AUTH = "replace-this-development-auth-secret"
_HARDENED = frozenset({"production", "prod", "staging", "preprod"})
_STAGING = frozenset({"staging", "preprod"})
# Settings default. Staging must not silently share this name with production.
_SHARED_DEFAULT_DATABASE = "mental_health"


def _explicit_database_name() -> str:
    return (os.environ.get("MONGO_DB_NAME") or os.environ.get("DATABASE_NAME") or "").strip()


def assert_environment_secrets(settings) -> None:
    env = str(getattr(settings, "APP_ENV", "") or "").strip().lower()
    if env not in _HARDENED:
        return
    if settings.ENCRYPTION_SECRET_KEY == _PLACEHOLDER_ENCRYPTION:
        raise RuntimeError("ENCRYPTION_SECRET_KEY must be set for this environment")
    if settings.AUTH_SIGNING_SECRET == _PLACEHOLDER_AUTH:
        raise RuntimeError("AUTH_SIGNING_SECRET must be set for this environment")
    db_name = _explicit_database_name()
    if not db_name:
        raise RuntimeError("DATABASE_NAME or MONGO_DB_NAME must be set for this environment")
    if env in _STAGING and db_name.lower() == _SHARED_DEFAULT_DATABASE:
        raise RuntimeError(
            "staging DATABASE_NAME must not use the shared default database name"
        )

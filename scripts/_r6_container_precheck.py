"""Round 6 staging precheck. Prints SET/UNSET only. Never prints secret values."""
from __future__ import annotations

import json
import os
from pathlib import Path

PLACEHOLDER_ENC = "gAAAAABl_secret_key_placeholder_32bytes_len="
PLACEHOLDER_AUTH = "replace-this-development-auth-secret"


def flag(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        return "UNSET"
    if value in {PLACEHOLDER_ENC, PLACEHOLDER_AUTH}:
        return "PLACEHOLDER"
    return "SET"


def present(path: str) -> bool:
    return Path(path).exists()


def main() -> None:
    uri = os.environ.get("MONGODB_URI") or ""
    mongo_host = "OTHER"
    if "@mongo:" in uri or "://mongo:" in uri or "://mongo/" in uri:
        mongo_host = "mongo"
    report = {
        "APP_ENV": os.environ.get("APP_ENV"),
        "DATABASE_NAME": os.environ.get("DATABASE_NAME"),
        "MONGO_DB_NAME": os.environ.get("MONGO_DB_NAME"),
        "mongo_hostname": mongo_host,
        "AWS_ACCESS_KEY_ID": flag("AWS_ACCESS_KEY_ID"),
        "AWS_SECRET_ACCESS_KEY": flag("AWS_SECRET_ACCESS_KEY"),
        "AWS_REGION": flag("AWS_REGION"),
        "SARVAM_API_KEY": flag("SARVAM_API_KEY"),
        "ENCRYPTION_SECRET_KEY": flag("ENCRYPTION_SECRET_KEY"),
        "AUTH_SIGNING_SECRET": flag("AUTH_SIGNING_SECRET"),
        "image_paths": {
            ".env": present("/app/.env") or present("/.env"),
            ".env.staging.local": present("/app/.env.staging.local"),
            ".git": present("/app/.git"),
            ".venv": present("/app/.venv"),
        },
        "cwd": os.getcwd(),
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

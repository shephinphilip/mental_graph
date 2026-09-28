"""Create gitignored local staging secrets. Never print values. Never overwrite."""

from __future__ import annotations

import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".env.staging.local"


def main() -> None:
    if TARGET.exists():
        print("staging env exists; not overwritten")
        return
    mongo_password = secrets.token_hex(24)
    encryption = secrets.token_urlsafe(48)
    auth = secrets.token_urlsafe(48)
    TARGET.write_text(
        "\n".join(
            [
                "# Generated local staging secrets. Do not commit.",
                "APP_ENV=staging",
                "DATABASE_NAME=zenark_staging",
                "MONGO_DB_NAME=zenark_staging",
                "MONGO_STAGING_PASSWORD=" + mongo_password,
                "ENCRYPTION_SECRET_KEY=" + encryption,
                "AUTH_SIGNING_SECRET=" + auth,
                "AWS_ACCESS_KEY_ID=",
                "AWS_SECRET_ACCESS_KEY=",
                "AWS_REGION=ap-south-1",
                "SARVAM_API_KEY=",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print("wrote gitignored staging env")


if __name__ == "__main__":
    main()

"""Production/staging must not start on development placeholders."""

from types import SimpleNamespace

import pytest

from config.config import Settings
from core.runtime_guard import assert_environment_secrets


def test_sarvam_default_is_empty():
    assert Settings.model_fields["SARVAM_API_KEY"].default == ""


def test_development_placeholders_are_allowed():
    assert_environment_secrets(
        SimpleNamespace(
            APP_ENV="development",
            ENCRYPTION_SECRET_KEY="gAAAAABl_secret_key_placeholder_32bytes_len=",
            AUTH_SIGNING_SECRET="replace-this-development-auth-secret",
        )
    )


def test_production_refuses_placeholder_auth_and_encryption():
    with pytest.raises(RuntimeError, match="ENCRYPTION_SECRET_KEY"):
        assert_environment_secrets(
            SimpleNamespace(
                APP_ENV="production",
                ENCRYPTION_SECRET_KEY="gAAAAABl_secret_key_placeholder_32bytes_len=",
                AUTH_SIGNING_SECRET="a-real-signing-secret-value",
            )
        )
    with pytest.raises(RuntimeError, match="AUTH_SIGNING_SECRET"):
        assert_environment_secrets(
            SimpleNamespace(
                APP_ENV="staging",
                ENCRYPTION_SECRET_KEY="a-real-encryption-secret-value",
                AUTH_SIGNING_SECRET="replace-this-development-auth-secret",
            )
        )


def _valid_secrets():
    return dict(
        ENCRYPTION_SECRET_KEY="a-real-encryption-secret-value",
        AUTH_SIGNING_SECRET="a-real-signing-secret-value",
    )


def test_hardened_env_requires_explicit_database_name(monkeypatch):
    monkeypatch.delenv("DATABASE_NAME", raising=False)
    monkeypatch.delenv("MONGO_DB_NAME", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_NAME"):
        assert_environment_secrets(
            SimpleNamespace(APP_ENV="staging", DATABASE_NAME="mental_health", **_valid_secrets())
        )


def test_staging_refuses_shared_default_database_name(monkeypatch):
    monkeypatch.setenv("DATABASE_NAME", "mental_health")
    monkeypatch.delenv("MONGO_DB_NAME", raising=False)
    with pytest.raises(RuntimeError, match="shared default"):
        assert_environment_secrets(SimpleNamespace(APP_ENV="staging", **_valid_secrets()))


def test_staging_accepts_a_distinct_database_name(monkeypatch):
    monkeypatch.setenv("DATABASE_NAME", "zenark_staging")
    monkeypatch.delenv("MONGO_DB_NAME", raising=False)
    assert_environment_secrets(SimpleNamespace(APP_ENV="staging", **_valid_secrets())) is None


def test_production_accepts_an_explicit_database_name(monkeypatch):
    monkeypatch.setenv("DATABASE_NAME", "mental_health")
    monkeypatch.delenv("MONGO_DB_NAME", raising=False)
    assert_environment_secrets(SimpleNamespace(APP_ENV="production", **_valid_secrets())) is None


def test_aws_and_mongo_defaults_are_not_live_credentials():
    aws_id = Settings.model_fields["AWS_ACCESS_KEY_ID"].default
    aws_secret = Settings.model_fields["AWS_SECRET_ACCESS_KEY"].default
    mongo = Settings.model_fields["MONGODB_URI"].default
    assert aws_id == ""
    assert aws_secret == ""
    assert "localhost" in mongo
    assert "@" not in mongo


def test_encryption_and_auth_defaults_are_known_placeholders_not_unique_secrets():
    assert (
        Settings.model_fields["ENCRYPTION_SECRET_KEY"].default
        == "gAAAAABl_secret_key_placeholder_32bytes_len="
    )
    assert (
        Settings.model_fields["AUTH_SIGNING_SECRET"].default
        == "replace-this-development-auth-secret"
    )


def test_env_and_venv_are_not_tracked():
    import subprocess

    env = subprocess.check_output(["git", "ls-files", "--", ".env"], text=True).strip()
    venv = subprocess.check_output(["git", "ls-files", "--", ".venv"], text=True).strip()
    assert env == ""
    assert venv == ""

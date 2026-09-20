"""
config.py — Centralised Application Configuration
===================================================

All runtime settings are sourced from environment variables or a ``.env``
file at the project root. A single ``Settings`` instance is created lazily
on first use and then cached for the lifetime of the process via
``@lru_cache``.

v0.4.0 Migration Note
---------------------
- Removed Gemini/OpenAI API key settings and Neo4j connection parameters.
- Sourced AWS Bedrock credentials and region.
- Sourced Sarvam fallback model configuration.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central application configuration, sourced from environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── AWS Bedrock (Primary LLM) ─────────────────────────────────────────────
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # ── Sarvam AI (Fallback LLM) ──────────────────────────────────────────────
    SARVAM_API_KEY: str = ""
    SARVAM_BASE_URL: Optional[str] = "https://api.sarvam.ai/v1"

    # ── MongoDB ──────────────────────────────────────────────────────────────
    MONGODB_URI: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "mental_health"

    # ── LLM Model Configuration ──────────────────────────────────────────────
    PRIMARY_MODEL: str = "google.gemma-2-9b-it"
    FALLBACK_MODEL: str = "sarvam-2b"
    LLM_TEMPERATURE: float = 0.7

    # ── MongoDB Graph Traversal ───────────────────────────────────────────────
    GRAPH_TRAVERSAL_DEPTH: int = 2  # Max hops for $graphLookup traversal

    # ── Security & Privacy ────────────────────────────────────────────────────
    ENCRYPTION_SECRET_KEY: str = "gAAAAABl_secret_key_placeholder_32bytes_len="
    ENFORCE_PII_ANONYMIZATION: bool = True

    # ── Crisis Helplines (India Default) ─────────────────────────────────────
    CRISIS_HELPLINE_TELE_MANAS: str = "14416"
    CRISIS_HELPLINE_AASRA: str = "+91-9820466726"
    CRISIS_HELPLINE_KIRAN: str = "1800-599-0019"
    CRISIS_HELPLINE_VANDREVALA: str = "+91-9999666555"

    # ── Conversation ──────────────────────────────────────────────────────────
    MAX_HISTORY_MESSAGES: int = 20
    MOOD_LOG_LOOKBACK_DAYS: int = 7


@lru_cache
def get_settings() -> Settings:
    """
    Return the application settings singleton.
    """
    return Settings()

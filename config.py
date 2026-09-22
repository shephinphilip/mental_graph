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

    # ── AWS Bedrock (Primary & Fallback LLMs) ─────────────────────────────────
    AWS_REGION: str = "ap-south-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # ── MongoDB ──────────────────────────────────────────────────────────────
    MONGODB_URI: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "mental_health"

    # ── Bedrock Model Identifiers ─────────────────────────────────────────────
    BEDROCK_MODEL: str = "google.gemma-3-27b-it"
    BEDROCK_GEMMA_MODEL_ID: str = "google.gemma-3-27b-it"
    BEDROCK_SARVAM_MODEL_ID: str = "sarvam.sarvam-m:0"

    PRIMARY_MODEL: str = "google.gemma-3-27b-it"
    # Backward-compatible alias. Runtime fallback selection is intentionally
    # pinned to BEDROCK_SARVAM_MODEL_ID in llm_provider.py.
    FALLBACK_MODEL: str = "sarvam.sarvam-m:0"
    LLM_TEMPERATURE: float = 0.7

    # ── MongoDB Graph Traversal ───────────────────────────────────────────────
    GRAPH_TRAVERSAL_DEPTH: int = 2  # Max hops for $graphLookup traversal

    # ── Security & Privacy ────────────────────────────────────────────────────
    ENCRYPTION_SECRET_KEY: str = "gAAAAABl_secret_key_placeholder_32bytes_len="
    ENFORCE_PII_ANONYMIZATION: bool = True
    AUTH_SIGNING_SECRET: str = "replace-this-development-auth-secret"
    AUTH_TOKEN_TTL_SECONDS: int = 43_200

    # ── Adaptive Psychological Memory ────────────────────────────────────────
    APM_RECOMMENDATION_CONFIDENCE: float = 0.4
    APM_TRIGGER_DECAY_PER_DAY: float = 0.002
    APM_EVOLUTION_DECAY_PER_DAY: float = 0.003
    APM_RECOVERY_DECAY_PER_DAY: float = 0.02
    APM_REINFORCEMENT_DECAY_PER_DAY: float = 0.01
    APM_FAILURE_PENALTY: float = 0.12

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

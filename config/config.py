"""
config.config — Canonical application Settings and logger
=========================================================

All runtime settings are sourced from environment variables or the ``.env``
file at the project root. A single ``Settings`` instance is created lazily
on first use and then cached for the lifetime of the process via
``@lru_cache``.

Callers should import the process logger as::

    from config.config import logger

``logger.info`` / ``logger.debug`` / ``logger.warning`` honour ``LOG_LEVEL``
from Settings (DEBUG, INFO, WARNING, plus ERROR/CRITICAL). The named
``zenark`` logger shares the root handlers configured by
``core.logging.configure_logging`` so formats do not double up.

v0.4.0 Migration Note
---------------------
- Removed Gemini/OpenAI API key settings and Neo4j connection parameters.
- Sourced AWS Bedrock credentials and region.
- Sourced Sarvam fallback model configuration.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root: config/config.py → config/ → project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Load the existing .env without overwriting variables already in the process.
load_dotenv(dotenv_path=ENV_FILE, override=False)

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOGGER_NAME = "zenark"
SUPPORTED_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def normalize_log_level(value: object) -> str:
    """Map env/Settings values onto standard library level names."""
    if value is None:
        return "INFO"
    level = str(value).strip().upper()
    if not level:
        return "INFO"
    if level == "WARN":
        return "WARNING"
    if level not in SUPPORTED_LOG_LEVELS:
        return "INFO"
    return level


def _build_logger(level_name: str) -> logging.Logger:
    """
    Named logger attached to the process root handlers.

    ``core.logging.configure_logging`` uses the same format via
    ``logging.basicConfig``. We only call ``basicConfig`` when the root
    logger has no handlers yet, so importing Settings never stacks a
    second handler or a second format.
    """
    level = getattr(logging, normalize_log_level(level_name), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format=LOG_FORMAT)
    log = logging.getLogger(LOGGER_NAME)
    log.setLevel(level)
    log.propagate = True
    return log


class Settings(BaseSettings):
    """
    Central application configuration, sourced from environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
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
    # Aliases from the production-readiness spec. Empty means "use the pair above".
    MONGO_URI: str = ""
    MONGO_DB_NAME: str = ""
    MONGO_MAX_POOL_SIZE: int = 100
    MONGO_MIN_POOL_SIZE: int = 5
    MONGO_SERVER_SELECTION_TIMEOUT_MS: int = 5_000
    MONGO_CONNECT_TIMEOUT_MS: int = 5_000
    MONGO_SOCKET_TIMEOUT_MS: int = 30_000

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

    # ── Longitudinal Pattern Detection Engine ────────────────────────────────
    # Evidence thresholds (documented — tune via env without code changes)
    PATTERN_EMERGING_MIN_EVIDENCE: int = 3
    PATTERN_ESTABLISHED_MIN_EVIDENCE: int = 5
    PATTERN_RETRIEVAL_MAX: int = 3
    PATTERN_RETRIEVAL_MIN_CONFIDENCE: float = 0.4
    PATTERN_DECAY_PER_DAY: float = 0.01
    PATTERN_INACTIVE_DAYS: int = 45
    PATTERN_BASELINE_WINDOW_DAYS: int = 30
    PATTERN_LOOKBACK_DAYS: int = 60
    SLEEP_CONTEXT_DAYS: int = 7
    JOURNAL_CONTEXT_LIMIT: int = 5
    JOURNAL_PREVIEW_CHARS: int = 180
    JOURNAL_CONTEXT_MAX_CHARS: int = 1200
    PATTERN_FEEDBACK_CONFIRM_BOOST: float = 0.08
    PATTERN_FEEDBACK_DISAGREE_PENALTY: float = 0.15
    PATTERN_CONTRADICTION_PENALTY: float = 0.10

    # ── Dynamic 1–10 risk window ─────────────────────────────────────────────
    RISK_HIGH_THRESHOLD: float = 8.0
    RISK_WINDOW_TURNS: int = 3
    RISK_ROLLING_HOURS: int = 6
    RISK_CARD_COOLDOWN_HOURS: int = 72

    # ── Professional consultation evaluation ─────────────────────────────────
    CONSULTATION_COOLDOWN_DAYS: int = 30
    CONSULTATION_LOOKBACK_DAYS: int = 14
    CONSULTATION_REPORT_METRIC_THRESHOLD: int = 7
    CONSULTATION_MIN_ELEVATED_REPORTS: int = 2
    CONSULTATION_MIN_RISK_TURNS: int = 6

    # ── Structured student memory ────────────────────────────────────────────
    MEMORY_CONTEXT_LIMIT: int = 8
    MEMORY_DECAY_PER_DAY: float = 0.01
    MEMORY_MIN_IMPORTANCE: float = 0.2

    # ── Meditation ranking ──────────────────────────────────────────────────
    # State match (PAD + latent) outweighs personal history so a new feeling
    # is not swallowed by an old favorite. Friction and cognitive load sit
    # next, because a long or demanding practice is a poor fit when the
    # person is overloaded. Personal success uses only explicit helpfulness.
    # Recency is a small time-of-day tie-break. Repetition and uncertainty
    # are subtracted so the same clip, or a shaky estimate, cannot win on
    # habit alone.
    MEDITATION_WEIGHT_PAD: float = 0.28
    MEDITATION_WEIGHT_LATENT: float = 0.24
    MEDITATION_WEIGHT_FRICTION: float = 0.16
    MEDITATION_WEIGHT_COGNITIVE: float = 0.12
    MEDITATION_WEIGHT_PERSONAL: float = 0.16
    MEDITATION_WEIGHT_RECENCY: float = 0.04
    MEDITATION_REPETITION_PENALTY: float = 0.18
    # Full penalty for 12 hours, then linear to zero at 72 hours, so a
    # practice used Monday can be offered again on Friday.
    MEDITATION_REPETITION_FULL_HOURS: float = 12
    MEDITATION_REPETITION_ZERO_HOURS: float = 72
    # When this user has no explicit helpfulness yet, the unused 0.16
    # personal-success weight moves onto PAD (+0.09) and latent match (+0.07).
    MEDITATION_COLD_START_PAD: float = 0.09
    MEDITATION_COLD_START_LATENT: float = 0.07
    # Catalog promotion: provisional -> empirically_validated.
    MEDITATION_VALIDATION_MIN_COMPLETIONS: int = 50
    MEDITATION_VALIDATION_MIN_HELPFUL_RATIO: float = 0.80
    MEDITATION_UNCERTAINTY_PENALTY: float = 0.12
    MEDITATION_LANGUAGE_BONUS: float = 0.05
    MEDITATION_MIN_CONFIDENCE: float = 0.42
    MEDITATION_MIN_SCORE: float = 0.45

    # ── Crisis Helplines (India Default) ─────────────────────────────────────
    CRISIS_HELPLINE_TELE_MANAS: str = "14416"
    CRISIS_HELPLINE_AASRA: str = "+91-9820466726"
    CRISIS_HELPLINE_KIRAN: str = "1800-599-0019"
    CRISIS_HELPLINE_VANDREVALA: str = "+91-9999666555"

    # ── Conversation ──────────────────────────────────────────────────────────
    MAX_HISTORY_MESSAGES: int = 20
    MOOD_LOG_LOOKBACK_DAYS: int = 7

    # ── Mood check-ins and habits ────────────────────────────────────────────
    # A miss is forgiven so a streak never becomes another thing to fail at.
    HABIT_GRACE_MISSES_PER_WEEK: int = 1
    HABIT_MAX_ACTIVE: int = 20
    MOOD_BACKFILL_MAX_DAYS: int = 30

    # ── Query bounds ─────────────────────────────────────────────────────────
    DEFAULT_LIMIT: int = 50
    MAX_LIMIT: int = 90

    # ── Environment ──────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    # Operational levels: DEBUG, INFO, WARNING. ERROR/CRITICAL also accepted.
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: str = ""
    TRUSTED_HOSTS: str = ""

    # ── Rate limiting (process-local unless RATE_LIMIT_BACKEND=redis) ────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_BACKEND: str = "memory"
    RATE_LIMIT_AUTH_PER_MINUTE: int = 20
    RATE_LIMIT_CHAT_PER_MINUTE: int = 60
    RATE_LIMIT_STREAM_PER_MINUTE: int = 30
    RATE_LIMIT_REPORT_PER_MINUTE: int = 10
    RATE_LIMIT_VOICE_STT_PER_MINUTE: int = 20
    RATE_LIMIT_VOICE_SESSION_PER_MINUTE: int = 10
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 300

    # ── Sarvam speech (STT / TTS). The API key never leaves the backend. ──
    SARVAM_API_KEY: str = "sk_591hdikc_WwGzsgMmBuV7JOGiqu7ah1iA"
    SARVAM_STT_MODEL: str = "saarika:v2.5"
    SARVAM_TTS_MODEL: str = "bulbul:v3"
    SARVAM_STT_URL: str = "https://api.sarvam.ai/speech-to-text"
    SARVAM_TTS_URL: str = "https://api.sarvam.ai/text-to-speech"
    VOICE_STT_TIMEOUT: float = 30.0
    VOICE_TTS_TIMEOUT: float = 30.0
    VOICE_LLM_TIMEOUT: float = 45.0
    MAX_AUDIO_FILE_SIZE: int = 10_485_760
    MAX_AUDIO_DURATION_SECONDS: int = 60
    MAX_CONCURRENT_VOICE_SESSIONS: int = 50
    VOICE_PROVIDER_CONCURRENCY: int = 8
    VOICE_TTS_SPEAKER: str = "anushka"
    VOICE_AUDIO_CHUNK_BYTES: int = 4096

    # ── LLM resilience ───────────────────────────────────────────────────────
    LLM_TIMEOUT_SECONDS: float = 45.0
    LLM_MAX_RETRIES: int = 2
    LLM_RETRY_BASE_SECONDS: float = 0.4
    LLM_CONCURRENCY: int = 16
    LLM_CIRCUIT_FAILURES: int = 8
    LLM_CIRCUIT_RESET_SECONDS: float = 30.0

    @field_validator("LOG_LEVEL", mode="before")
    @classmethod
    def _validate_log_level(cls, value: object) -> str:
        return normalize_log_level(value)


@lru_cache
def get_settings() -> Settings:
    """
    Return the application settings singleton.
    """
    return Settings()


settings = get_settings()
logger = _build_logger(settings.LOG_LEVEL)

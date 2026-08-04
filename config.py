"""
config.py — Centralised Application Configuration
===================================================

All runtime settings are sourced from environment variables or a ``.env``
file at the project root.  A single ``Settings`` instance is created lazily
on first use and then cached for the lifetime of the process via
``@lru_cache``, so there is zero overhead from repeated calls.

Environment variable lookup priority (highest → lowest):
  1. Shell environment (export / .env injected by Docker / systemd)
  2. ``.env`` file in the project root
  3. Default values defined in the class body

Usage
-----
    from config import get_settings

    settings = get_settings()
    uri = settings.MONGODB_URI
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central application configuration, sourced from environment variables.

    Each field maps 1-to-1 with an environment variable of the same name
    (case-insensitive on most systems).  Override any field by setting the
    corresponding variable in ``.env`` or the shell environment.

    Attributes
    ----------
    GEMINI_API_KEY : str
        Google Gemini API key.  Required for the primary LLM.
    OPENAI_API_KEY : str
        OpenAI API key.  Required for the fallback LLM (GPT-4o).
    MONGODB_URI : str
        MongoDB connection string.  Accepts both local and Atlas URIs.
    DATABASE_NAME : str
        Name of the MongoDB database to use.
    PRIMARY_MODEL : str
        Primary LLM model identifier (e.g. ``"gemini-1.5-pro"``).
    FALLBACK_MODEL : str
        Fallback LLM model identifier (e.g. ``"gpt-4o"``).
    LLM_TEMPERATURE : float
        Sampling temperature shared by both LLMs (0 = deterministic).
    NEO4J_URI : str
        Neo4j connection URI.  Use ``neo4j+s://`` for AuraDB.
    NEO4J_USER : str
        Neo4j username.
    NEO4J_PASSWORD : str
        Neo4j password.
    GRAPH_TRAVERSAL_DEPTH : int
        Maximum number of hops to traverse from the User node when
        building the Graph RAG context.
    ENCRYPTION_SECRET_KEY : str
        Secret used to derive the Fernet encryption key via PBKDF2.
        **Must** be at least 32 bytes of randomness in production.
    ENFORCE_PII_ANONYMIZATION : bool
        When ``True``, phone numbers, emails, and Aadhaar numbers are
        redacted before text is sent to external LLM APIs.
    CRISIS_HELPLINE_* : str
        Phone numbers for the four supported crisis helplines (India).
        Surfaced in the UI and injected into the system prompt.
    MAX_HISTORY_MESSAGES : int
        Maximum number of past messages loaded from MongoDB per session.
    MOOD_LOG_LOOKBACK_DAYS : int
        Window (in days) used when fetching recent mood log entries.
    """

    # ── Pydantic-settings meta ───────────────────────────────────────────────
    model_config = SettingsConfigDict(
        # Load variables from this file if it exists (ignored silently if absent)
        env_file=".env",
        env_file_encoding="utf-8",
        # Silently ignore any env vars not declared here (avoids noisy errors
        # when the shell exports unrelated variables)
        extra="ignore",
    )

    # ── API Keys ─────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # ── MongoDB ──────────────────────────────────────────────────────────────
    MONGODB_URI: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "mental_health"

    # ── LLM ──────────────────────────────────────────────────────────────────
    PRIMARY_MODEL: str = "gemini-1.5-pro"
    FALLBACK_MODEL: str = "gpt-4o"
    LLM_TEMPERATURE: float = 0.7  # 0 = deterministic, 1 = most creative

    # ── Neo4j (Graph RAG) ─────────────────────────────────────────────────────
    NEO4J_URI: str = "neo4j://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "password"
    GRAPH_TRAVERSAL_DEPTH: int = 2  # k-hop depth for subgraph retrieval

    # ── Security & Privacy ────────────────────────────────────────────────────
    ENCRYPTION_SECRET_KEY: str = "gAAAAABl_secret_key_placeholder_32bytes_len="
    ENFORCE_PII_ANONYMIZATION: bool = True  # Redact PII before LLM calls

    # ── Crisis Helplines (India Default) ─────────────────────────────────────
    # These are surfaced in the UI sidebar and injected into crisis_alert SSE
    # events.  Override in `.env` for non-Indian deployments.
    CRISIS_HELPLINE_TELE_MANAS: str = "14416"
    CRISIS_HELPLINE_AASRA: str = "+91-9820466726"
    CRISIS_HELPLINE_KIRAN: str = "1800-599-0019"
    CRISIS_HELPLINE_VANDREVALA: str = "+91-9999666555"

    # ── Conversation ──────────────────────────────────────────────────────────
    MAX_HISTORY_MESSAGES: int = 20   # Max messages loaded per session for LLM context
    MOOD_LOG_LOOKBACK_DAYS: int = 7  # Days of mood log history to include in prompt


@lru_cache
def get_settings() -> Settings:
    """
    Return the application settings singleton.

    Uses ``@lru_cache`` so that pydantic-settings only parses the environment
    and ``.env`` file **once** per process.  Calling this function from
    multiple modules is free after the first invocation.

    Returns
    -------
    Settings
        The cached ``Settings`` instance.

    Raises
    ------
    pydantic.ValidationError
        If any required setting has an incompatible type after coercion
        (e.g. a non-numeric value for ``LLM_TEMPERATURE``).
    """
    return Settings()

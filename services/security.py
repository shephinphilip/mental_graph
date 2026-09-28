"""
services/security.py — Encryption and PII Anonymization Layer
==============================================================

Provides two complementary security functions for protecting sensitive
user data before it leaves the application boundary:

1. At-Rest Payload Encryption / Decryption (Fernet / AES-128-CBC)
----------------------------------------------------------------
   This is server-side encryption at rest, not end-to-end encryption.
   The server holds the key and reads content in the clear to generate
   replies, detect crisis signals, and compute insights.  See
   docs/ENCRYPTION.md for the boundary and its known weaknesses.

   ``encrypt_payload(text)`` and ``decrypt_payload(token)``

   Encrypts message content before writing to MongoDB and decrypts when
   reading back.  Uses Fernet (a symmetric authenticated encryption scheme
   from the ``cryptography`` library) with a 32-byte key derived from the
   ``ENCRYPTION_SECRET_KEY`` setting via PBKDF2-HMAC-SHA256 (100,000
   iterations).

   Encrypted payloads are prefixed with ``"enc::"`` so they can be
   distinguished from unencrypted strings (e.g. legacy data or test data).

   Encryption failures raise ``CryptoIntegrityError`` and must not persist
   plaintext. Decryption of ``enc::`` values that cannot be opened with the
   current key also raises; ciphertext is never returned to API callers.

2. PII Anonymization (Regex Redaction)
---------------------------------------
   ``anonymize_text(text)``

   Redacts three categories of personally identifiable information before
   text is sent to external LLM APIs (Gemini / OpenAI):
   - Indian mobile phone numbers (10-digit, with optional +91 prefix)
   - Email addresses (standard RFC-5322 pattern)
   - Aadhaar numbers (12-digit, with optional space/hyphen separators)

   Controlled by the ``ENFORCE_PII_ANONYMIZATION`` configuration flag.
   Set it to ``False`` in development to disable redaction.

Security note
-------------
The static salt (``b"mental_health_salt_2026"``) in ``_get_fernet_key``
is intentional for determinism — rotating the salt would invalidate all
existing encrypted messages.  Change it only during a planned re-encryption
migration.  The security of the scheme rests primarily on the entropy of
``ENCRYPTION_SECRET_KEY``.  Use a 32+ byte random value in production.
"""

import base64
import re
from typing import Tuple

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from config.config import get_settings, logger

# ── PII Redaction Patterns ────────────────────────────────────────────────────
# Pre-compiled at module level for performance (applied on every streamed message).

# Indian mobile: optional +91 prefix, then a digit starting 6-9, then 9 more digits.
# Also matches US-style 10-digit XXX-XXX-XXXX and XXX XXX XXXX formats.
_PHONE_PATTERN = re.compile(
    r"(?:\+91[\-\s]?)?[6-9]\d{9}|\b\d{3}[\-\s]?\d{3}[\-\s]?\d{4}\b"
)

# Standard email address pattern (RFC-5322 simplified)
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)

# Aadhaar number: 12 digits optionally split by spaces or hyphens into groups of 4
_AADHAAR_PATTERN = re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")

_PLACEHOLDER_ENCRYPTION = "gAAAAABl_secret_key_placeholder_32bytes_len="


class CryptoIntegrityError(RuntimeError):
    """Encryption or decryption failed. The message must never include a payload."""


def _log_crypto_failure(operation: str, exc: BaseException) -> None:
    logger.error("%s failed type=%s", operation, type(exc).__name__)


# ── Encryption Helper ─────────────────────────────────────────────────────────


def _get_fernet_key(secret: str) -> bytes:
    """
    Derive a 32-byte URL-safe base64 Fernet key from the application secret.

    Uses PBKDF2-HMAC-SHA256 with 100,000 iterations to stretch a
    potentially low-entropy secret into a cryptographically strong key.

    Parameters
    ----------
    secret : str
        The raw secret string from ``Settings.ENCRYPTION_SECRET_KEY``.
        Should be a long, random value in production.

    Returns
    -------
    bytes
        A 44-byte URL-safe base64-encoded key suitable for ``Fernet(key)``.

    Raises
    ------
    ValueError
        If the PBKDF2 derivation fails (extremely unlikely with valid input).

    Security note
    -------------
    The salt (``b"mental_health_salt_2026"``) is static and deterministic.
    This is intentional — the same secret must always produce the same key
    to decrypt existing data.  In a multi-tenant deployment, consider
    per-user salts stored alongside the encrypted data.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,                          # 32 bytes → 256-bit key
        salt=b"mental_health_salt_2026",    # Static salt (see Security note in module docstring)
        iterations=100_000,                 # NIST-recommended minimum for PBKDF2
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode()))


def encrypt_payload(text: str) -> str:
    """
    Encrypt a plaintext string and return a Fernet token prefixed with ``"enc::"``.

    Called before writing message content to MongoDB to ensure data is
    encrypted at rest.  The ``"enc::"`` prefix allows ``decrypt_payload``
    to detect and skip non-encrypted strings (e.g. legacy unencrypted data).

    Parameters
    ----------
    text : str
        The plaintext string to encrypt.  Empty strings are returned
        unchanged (no-op).

    Returns
    -------
    str
        The encrypted token as ``"enc::<fernet_token>"``, where the Fernet
        token is URL-safe base64 encoded.

    Raises
    ------
    CryptoIntegrityError
        If the key is refused or Fernet cannot encrypt. Callers must abort
        the write. Plaintext is never returned.

    Example
    -------
    ::

        enc = encrypt_payload("I feel anxious today")
        # → "enc::gAAAAA..."
    """
    # Treat empty strings as a no-op to avoid unnecessary encryption overhead
    if not text:
        return text

    settings = get_settings()
    if (
        settings.APP_ENV.lower() in {"production", "prod"}
        and settings.ENCRYPTION_SECRET_KEY == _PLACEHOLDER_ENCRYPTION
    ):
        _log_crypto_failure("Encryption", RuntimeError("placeholder"))
        raise CryptoIntegrityError("Encryption failed")

    try:
        key = _get_fernet_key(settings.ENCRYPTION_SECRET_KEY)
        f = Fernet(key)
        encrypted_bytes = f.encrypt(text.encode("utf-8"))
        sealed = f"enc::{encrypted_bytes.decode('utf-8')}"
    except CryptoIntegrityError:
        raise
    except Exception as exc:
        _log_crypto_failure("Encryption", exc)
        raise CryptoIntegrityError("Encryption failed") from None
    if not sealed.startswith("enc::"):
        _log_crypto_failure("Encryption", RuntimeError("missing_prefix"))
        raise CryptoIntegrityError("Encryption failed")
    return sealed


def decrypt_payload(token: str) -> str:
    """
    Decrypt a Fernet token string back to the original plaintext.

    Checks for the ``"enc::"`` prefix to determine whether the string is
    encrypted.  Strings without the prefix (e.g. legacy unencrypted records,
    test data) are returned as-is.

    Parameters
    ----------
    token : str
        The token string from MongoDB.  May or may not have the ``"enc::"``
        prefix — both cases are handled.

    Returns
    -------
    str
        The original plaintext if decryption succeeds. Legacy strings
        without the ``enc::`` prefix are returned unchanged.

    Raises
    ------
    CryptoIntegrityError
        If an ``enc::`` value cannot be opened with the current key.
        Ciphertext is never returned.

    Example
    -------
    ::

        plaintext = decrypt_payload("enc::gAAAAA...")
        # → "I feel anxious today"

        plaintext = decrypt_payload("plain string")
        # → "plain string" (returned unchanged)
    """
    # If the token doesn't start with our prefix, it's not encrypted — return as-is.
    # This handles legacy data written before encryption was introduced.
    if not token or not token.startswith("enc::"):
        return token

    try:
        raw_token = token[5:]
        settings = get_settings()
        key = _get_fernet_key(settings.ENCRYPTION_SECRET_KEY)
        f = Fernet(key)
        decrypted_bytes = f.decrypt(raw_token.encode("utf-8"))
        return decrypted_bytes.decode("utf-8")
    except CryptoIntegrityError:
        raise
    except Exception as exc:
        _log_crypto_failure("Decryption", exc)
        raise CryptoIntegrityError("Decryption failed") from None


def seal_text(text: str) -> str:
    """Encrypt sensitive text. Already-sealed values are left unchanged."""
    if not text or str(text).startswith("enc::"):
        return text
    return encrypt_payload(text)


def open_text(text: str) -> str:
    """Decrypt a sealed field. Legacy plaintext is returned as stored."""
    if not text:
        return text or ""
    return decrypt_payload(text)


# ── PII Anonymization ─────────────────────────────────────────────────────────


def anonymize_text(text: str) -> str:
    """
    Redact PII from user-generated text before external LLM API calls.

    Applies three regex substitutions sequentially:
    1. Phone numbers → ``[PHONE_REDACTED]``
    2. Email addresses → ``[EMAIL_REDACTED]``
    3. Aadhaar numbers → ``[ID_REDACTED]``

    Controlled by the ``ENFORCE_PII_ANONYMIZATION`` configuration flag.
    When disabled (e.g. in local development), this function is a no-op
    and returns ``text`` unchanged.

    Parameters
    ----------
    text : str
        The raw user message text that may contain PII.

    Returns
    -------
    str
        The anonymized text with PII replaced by redaction tokens.
        Returns ``text`` unchanged if:
        - ``text`` is empty / falsy
        - ``ENFORCE_PII_ANONYMIZATION`` is ``False``

    Raises
    ------
    None
        This function never raises; regex failures are inherently impossible
        with pre-compiled patterns and valid string inputs.

    Example
    -------
    ::

        result = anonymize_text("Call me at +91 9876543210 or user@gmail.com")
        # → "Call me at [PHONE_REDACTED] or [EMAIL_REDACTED]"
    """
    # No-op for empty input
    if not text:
        return text

    settings = get_settings()

    # Skip anonymization if disabled in configuration (e.g. local dev)
    if not settings.ENFORCE_PII_ANONYMIZATION:
        return text

    # Apply substitutions sequentially; each step feeds into the next
    anonymized = _PHONE_PATTERN.sub("[PHONE_REDACTED]", text)
    anonymized = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", anonymized)
    anonymized = _AADHAAR_PATTERN.sub("[ID_REDACTED]", anonymized)

    return anonymized

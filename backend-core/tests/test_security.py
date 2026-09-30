"""
Unit tests for security, encryption, and anonymization layer.

Covers:
    - At-rest encryption/decryption roundtrips (Fernet AES-128-CBC)
    - Non-encrypted tokens pass through cleanly
    - PII anonymization (phones, emails, Aadhaar IDs)
"""

import pytest
from backend_core.security import anonymize_text, decrypt_payload, encrypt_payload


def test_encryption_roundtrip():
    original_text = "I am feeling extremely anxious about my work evaluation."
    encrypted = encrypt_payload(original_text)

    assert encrypted != original_text
    assert encrypted.startswith("enc::")

    decrypted = decrypt_payload(encrypted)
    assert decrypted == original_text


def test_decrypt_plain_text_passthrough():
    plain = "Regular unencrypted string"
    assert decrypt_payload(plain) == plain


def test_production_encrypt_refuses_placeholder_key(monkeypatch):
    from types import SimpleNamespace

    import backend_core.security as security

    monkeypatch.setattr(
        security,
        "get_settings",
        lambda: SimpleNamespace(
            APP_ENV="production",
            ENCRYPTION_SECRET_KEY="gAAAAABl_secret_key_placeholder_32bytes_len=",
        ),
    )
    with pytest.raises(security.CryptoIntegrityError, match="Encryption failed"):
        security.encrypt_payload("not-a-secret-payload")


def test_anonymize_phone_number():
    text = "Call me at +91 9876543210 or 9876543210 if needed."
    anonymized = anonymize_text(text)

    assert "+91 9876543210" not in anonymized
    assert "9876543210" not in anonymized
    assert "[PHONE_REDACTED]" in anonymized


def test_anonymize_email():
    text = "My email is user.test@example.com for follow ups."
    anonymized = anonymize_text(text)

    assert "user.test@example.com" not in anonymized
    assert "[EMAIL_REDACTED]" in anonymized


def test_anonymize_aadhaar():
    text = "My ID number is 1234 5678 9012 for reference."
    anonymized = anonymize_text(text)

    assert "1234 5678 9012" not in anonymized
    assert "[ID_REDACTED]" in anonymized

"""Authentication token and ownership tests."""

import pytest

from services.users import issue_access_token, verify_access_token


def test_access_token_round_trip():
    token = issue_access_token("student_123")
    assert verify_access_token(token) == "student_123"


def test_access_token_rejects_tampering():
    token = issue_access_token("student_123")
    body, signature = token.split(".")
    tampered = f"{body[:-1]}A.{signature}"
    with pytest.raises(ValueError, match="invalid or expired"):
        verify_access_token(tampered)

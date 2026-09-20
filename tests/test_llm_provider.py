"""
tests/test_llm_provider.py — Tests for AWS Bedrock + Sarvam LLM Provider
========================================================================

Tests:
  - Startup validation fails loudly when Sarvam fallback is missing API key
  - Primary LLM initialization
  - Fallback LLM initialization
"""

import sys
import pytest
from unittest.mock import MagicMock, patch

from config import get_settings
from llm_provider import get_fallback_llm, get_primary_llm, validate_llm_configuration


def test_validate_llm_configuration_missing_sarvam_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "FALLBACK_MODEL", "sarvam-2b")
    monkeypatch.setattr(get_settings(), "SARVAM_API_KEY", "")

    with pytest.raises(ValueError, match="SARVAM_API_KEY is missing"):
        validate_llm_configuration()


def test_validate_llm_configuration_valid_sarvam_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "FALLBACK_MODEL", "sarvam-2b")
    monkeypatch.setattr(get_settings(), "SARVAM_API_KEY", "sk-sarvam-test-123")

    # Should not raise any error
    validate_llm_configuration()


def test_get_primary_llm(monkeypatch):
    monkeypatch.setattr(get_settings(), "PRIMARY_MODEL", "google.gemma-2-9b-it")
    monkeypatch.setattr(get_settings(), "AWS_REGION", "us-east-1")

    mock_aws = MagicMock()
    mock_aws.ChatBedrockConverse = MagicMock(return_value="mock_llm")

    with patch.dict(sys.modules, {"langchain_aws": mock_aws}):
        llm = get_primary_llm()
        assert llm == "mock_llm"

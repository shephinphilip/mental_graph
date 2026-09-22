"""
tests/test_llm_provider.py — Tests for Exclusive AWS Bedrock LLM Provider
========================================================================

Tests:
  - Startup validation with Bedrock configuration
  - Primary LLM initialization with Bedrock model (google.gemma-3-27b-it)
  - Fallback LLM initialization with Bedrock model
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from config import get_settings
from llm_provider import get_fallback_llm, get_primary_llm, validate_llm_configuration


def test_validate_llm_configuration(monkeypatch):
    monkeypatch.setattr(get_settings(), "AWS_ACCESS_KEY_ID", "test_key")
    monkeypatch.setattr(get_settings(), "AWS_SECRET_ACCESS_KEY", "test_secret")

    # Should not raise any error
    validate_llm_configuration()


def test_get_primary_llm(monkeypatch):
    monkeypatch.setattr(get_settings(), "BEDROCK_MODEL", "google.gemma-3-27b-it")
    monkeypatch.setattr(get_settings(), "PRIMARY_MODEL", "google.gemma-3-27b-it")
    monkeypatch.setattr(get_settings(), "AWS_REGION", "ap-south-1")

    mock_aws = MagicMock()
    mock_aws.ChatBedrockConverse = MagicMock(return_value="mock_llm")

    with patch.dict(sys.modules, {"langchain_aws": mock_aws}):
        llm = get_primary_llm()
        assert llm == "mock_llm"


def test_get_fallback_llm(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "BEDROCK_SARVAM_MODEL_ID", "sarvam.sarvam-m:0"
    )
    # A conflicting legacy value must never affect runtime selection.
    monkeypatch.setattr(
        get_settings(), "FALLBACK_MODEL", "google.gemma-3-27b-it"
    )
    monkeypatch.setattr(get_settings(), "AWS_REGION", "ap-south-1")

    mock_aws = MagicMock()
    mock_aws.ChatBedrockConverse = MagicMock(return_value="mock_fallback_llm")

    with patch.dict(sys.modules, {"langchain_aws": mock_aws}):
        llm = get_fallback_llm()
        assert llm == "mock_fallback_llm"
        kwargs = mock_aws.ChatBedrockConverse.call_args.kwargs
        assert kwargs["model"] == "sarvam.sarvam-m:0"


def test_fallback_rejects_non_sarvam_model(monkeypatch):
    monkeypatch.setattr(
        get_settings(), "BEDROCK_SARVAM_MODEL_ID", "google.gemma-3-27b-it"
    )

    with pytest.raises(ValueError, match="must identify a Sarvam model"):
        get_fallback_llm()

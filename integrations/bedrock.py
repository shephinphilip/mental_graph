"""Bedrock entry used by graph, streaming, reports, and extraction.

Provider construction stays in ``llm_provider.py`` so existing tests that
patch that module keep working. This module is the documented integration
boundary for timeouts and retries.
"""

from llm_provider import (
    get_fallback_llm,
    get_llm,
    get_primary_llm,
    sanitize_messages_for_bedrock,
    validate_llm_configuration,
)

__all__ = [
    "get_fallback_llm",
    "get_llm",
    "get_primary_llm",
    "sanitize_messages_for_bedrock",
    "validate_llm_configuration",
]

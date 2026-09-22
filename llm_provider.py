"""
llm_provider.py — Exclusive AWS Bedrock LLM Provider
====================================================

All LLM calls are routed exclusively through Amazon Bedrock (via ChatBedrockConverse).
No direct Gemini or OpenAI API integrations are used.

Exposes:
  - ``get_primary_llm()``: Returns AWS Bedrock Chat model instance (e.g. google.gemma-3-27b-it).
  - ``get_fallback_llm()``: Returns the explicitly configured Sarvam model.
  - ``get_llm()``: Returns primary Bedrock LLM with Bedrock fallback attached.
  - ``validate_llm_configuration()``: Verifies AWS credentials at startup.
  - ``sanitize_messages_for_bedrock()``: Formats messages for Bedrock Converse API constraints.
"""

import logging
from config import get_settings

logger = logging.getLogger(__name__)


def validate_llm_configuration() -> None:
    """
    Validate AWS Bedrock configuration at app startup.
    """
    settings = get_settings()
    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        logger.warning(
            "AWS Bedrock credentials (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) are missing in environment. "
            "AWS Bedrock client will attempt to rely on default AWS CLI credentials or IAM role if available."
        )


def sanitize_messages_for_bedrock(messages: list) -> list:
    """
    Ensure SystemMessage is at the start, conversation begins with a HumanMessage,
    and consecutive messages of identical types are merged to satisfy AWS Bedrock Converse API's
    strict role-alternation requirement (user/assistant/user/assistant/...).
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    if not messages:
        return []

    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system_msgs = [m for m in messages if not isinstance(m, SystemMessage)]

    # Drop leading AIMessages because Bedrock conversation must start with a user message
    while non_system_msgs and isinstance(non_system_msgs[0], AIMessage):
        non_system_msgs.pop(0)

    if not non_system_msgs:
        return system_msgs

    # Merge consecutive messages of identical type
    merged = []
    for m in non_system_msgs:
        if not merged:
            merged.append(m)
        elif type(m) is type(merged[-1]):
            prev_content = merged[-1].content if hasattr(merged[-1], "content") else str(merged[-1])
            new_content = m.content if hasattr(m, "content") else str(m)
            merged[-1].content = f"{prev_content}\n{new_content}"
        else:
            merged.append(m)

    return system_msgs + merged


def _create_bedrock_llm(model_id: str):
    """
    Helper to instantiate a ChatBedrockConverse instance for the specified model_id.
    """
    settings = get_settings()
    try:
        from langchain_aws import ChatBedrockConverse
        kwargs = {
            "model": model_id,
            "region_name": settings.AWS_REGION,
            "temperature": settings.LLM_TEMPERATURE,
            "disable_streaming": False,
        }
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        return ChatBedrockConverse(**kwargs)
    except ImportError:
        import langchain_aws
        chat_cls = getattr(langchain_aws, "ChatBedrockConverse", getattr(langchain_aws, "ChatBedrock"))
        kwargs = {
            "model": model_id,
            "region_name": settings.AWS_REGION,
            "temperature": settings.LLM_TEMPERATURE,
            "disable_streaming": False,
        }
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        return chat_cls(**kwargs)


def get_primary_llm():
    """
    Return primary AWS Bedrock LLM instance (e.g. google.gemma-3-27b-it).
    """
    validate_llm_configuration()
    settings = get_settings()
    primary_model_id = (
        getattr(settings, "BEDROCK_MODEL", None)
        or getattr(settings, "BEDROCK_GEMMA_MODEL_ID", None)
        or settings.PRIMARY_MODEL
        or "google.gemma-3-27b-it"
    )
    return _create_bedrock_llm(primary_model_id)


def get_fallback_llm():
    """
    Return the configured Sarvam fallback.

    The fallback is deliberately sourced only from
    ``BEDROCK_SARVAM_MODEL_ID``.  It never falls through to
    ``FALLBACK_MODEL`` or another Gemma model.
    """
    validate_llm_configuration()
    settings = get_settings()
    fallback_model_id = settings.BEDROCK_SARVAM_MODEL_ID.strip()
    if not fallback_model_id:
        raise ValueError("BEDROCK_SARVAM_MODEL_ID must be configured")
    if not fallback_model_id.lower().startswith("sarvam."):
        raise ValueError(
            "BEDROCK_SARVAM_MODEL_ID must identify a Sarvam model; "
            f"got {fallback_model_id!r}"
        )
    return _create_bedrock_llm(fallback_model_id)


def get_llm():
    """
    Build and return a LangChain Runnable using AWS Bedrock models with failover.
    """
    primary = get_primary_llm()
    try:
        fallback = get_fallback_llm()
        settings = get_settings()
        primary_id = (
            settings.BEDROCK_MODEL
            or settings.BEDROCK_GEMMA_MODEL_ID
            or settings.PRIMARY_MODEL
        )
        fallback_id = settings.BEDROCK_SARVAM_MODEL_ID
        if primary_id != fallback_id:
            return primary.with_fallbacks([fallback])
        return primary
    except Exception as exc:
        logger.warning(
            "Fallback Bedrock LLM initialisation skipped or failed (%s); running with primary Bedrock LLM only",
            exc,
        )
        return primary

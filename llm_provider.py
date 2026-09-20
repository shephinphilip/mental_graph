"""
llm_provider.py — AWS Bedrock Primary & Sarvam Fallback LLM Provider
====================================================================

v0.4.0 Migration Note
---------------------
- Replaced Google Gemini with AWS Bedrock (Gemma 2 / Llama 3).
- Replaced OpenAI GPT-4o fallback with Sarvam AI.
- Startup validation enforces explicit Sarvam configuration to avoid silent fallbacks.

Exposes:
  - ``get_primary_llm()``: Returns AWS Bedrock Chat model instance.
  - ``get_fallback_llm()``: Returns Sarvam AI Chat model instance.
  - ``get_llm()``: Returns primary LLM with fallback chain attached.
  - ``validate_llm_configuration()``: Verifies LLM credentials at startup.
"""

import logging
from config import get_settings

logger = logging.getLogger(__name__)


def validate_llm_configuration() -> None:
    """
    Validate LLM provider configuration at app startup.

    Raises
    ------
    ValueError
        If fallback model is configured to Sarvam but SARVAM_API_KEY is missing.
    """
    settings = get_settings()

    if "sarvam" in settings.FALLBACK_MODEL.lower():
        if not settings.SARVAM_API_KEY:
            raise ValueError(
                f"FALLBACK_MODEL is configured as '{settings.FALLBACK_MODEL}', but SARVAM_API_KEY "
                "is missing. Fail-safe policy: Sarvam fallback must be explicitly configured with credentials."
            )


def get_primary_llm():
    """
    Return AWS Bedrock LLM instance.
    """
    settings = get_settings()
    try:
        from langchain_aws import ChatBedrockConverse
        kwargs = {
            "model": settings.PRIMARY_MODEL,
            "region_name": settings.AWS_REGION,
            "temperature": settings.LLM_TEMPERATURE,
        }
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        return ChatBedrockConverse(**kwargs)
    except ImportError:
        import langchain_aws
        chat_cls = getattr(langchain_aws, "ChatBedrockConverse", getattr(langchain_aws, "ChatBedrock"))
        kwargs = {
            "model": settings.PRIMARY_MODEL,
            "region_name": settings.AWS_REGION,
            "temperature": settings.LLM_TEMPERATURE,
        }
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
            kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
        return chat_cls(**kwargs)


def get_fallback_llm():
    """
    Return Sarvam AI fallback LLM instance.

    Raises
    ------
    ValueError
        If Sarvam API credentials are not set.
    """
    validate_llm_configuration()
    settings = get_settings()

    if "sarvam" in settings.FALLBACK_MODEL.lower():
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.FALLBACK_MODEL,
            openai_api_key=settings.SARVAM_API_KEY,
            openai_api_base=settings.SARVAM_BASE_URL or "https://api.sarvam.ai/v1",
            temperature=settings.LLM_TEMPERATURE,
        )
    else:
        # AWS Bedrock alternate model fallback
        try:
            from langchain_aws import ChatBedrockConverse
            kwargs = {
                "model": settings.FALLBACK_MODEL,
                "region_name": settings.AWS_REGION,
                "temperature": settings.LLM_TEMPERATURE,
            }
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
                kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
            return ChatBedrockConverse(**kwargs)
        except ImportError:
            import langchain_aws
            chat_cls = getattr(langchain_aws, "ChatBedrockConverse", getattr(langchain_aws, "ChatBedrock"))
            kwargs = {
                "model": settings.FALLBACK_MODEL,
                "region_name": settings.AWS_REGION,
                "temperature": settings.LLM_TEMPERATURE,
            }
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                kwargs["aws_access_key_id"] = settings.AWS_ACCESS_KEY_ID
                kwargs["aws_secret_access_key"] = settings.AWS_SECRET_ACCESS_KEY
            return chat_cls(**kwargs)


def get_llm():
    """
    Build and return a LangChain Runnable with AWS Bedrock -> Sarvam failover.
    """
    primary = get_primary_llm()
    try:
        fallback = get_fallback_llm()
        return primary.with_fallbacks([fallback])
    except Exception as exc:
        logger.warning(
            "Fallback LLM initialisation skipped or failed (%s); running with primary LLM only",
            exc,
        )
        return primary

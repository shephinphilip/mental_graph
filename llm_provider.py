"""
llm_provider.py — Dual-LLM Provider with Automatic Failover
=============================================================

Exposes a single public function, ``get_llm()``, that returns a
LangChain ``Runnable`` pre-configured with a **primary → fallback** chain:

  Primary  : Google Gemini (default: ``gemini-1.5-pro``)
  Fallback : OpenAI       (default: ``gpt-4o``)

The failover is handled transparently by LangChain's
``Runnable.with_fallbacks()``, which catches any exception raised by the
primary model and retries with the first fallback.  This covers:
  - Rate-limit errors (HTTP 429)
  - Model service outages
  - Timeout exceptions
  - API key / quota errors

Design notes
------------
* **Lazy imports**: ``langchain_google_genai`` and ``langchain_openai``
  are imported *inside* ``get_llm()`` rather than at module level.  This
  avoids pulling in heavy transitive dependencies (e.g. PyTorch DLL
  initialisation on Windows) during unit tests or CLI tools that import
  other modules but never call ``get_llm()``.

* **Both models share the same temperature** from ``Settings``.  If you
  need different temperatures per model, create separate ``Settings``
  fields and pass them individually.

* The returned object is **not cached** — callers receive a fresh instance
  each time.  Caching is deferred to the call sites that need it (e.g.
  the LangGraph nodes).
"""

import logging

from config import get_settings

logger = logging.getLogger(__name__)


def get_llm():
    """
    Build and return a LangChain Runnable with automatic LLM failover.

    The returned runnable behaves like any LangChain ``Runnable`` and
    supports both ``.invoke()`` (sync) and ``.ainvoke()`` / ``.astream()``
    (async).

    Failover behaviour
    ------------------
    If the primary Gemini model raises *any* exception the fallback OpenAI
    model is invoked automatically with the same input messages.  Both
    models share the temperature defined in the application settings.

    Returns
    -------
    langchain_core.runnables.RunnableWithFallbacks
        A LangChain runnable that calls Gemini first and falls back to
        GPT-4o on any failure.

    Raises
    ------
    ImportError
        If ``langchain_google_genai`` or ``langchain_openai`` are not
        installed.  Add the missing package to ``requirements.txt`` and
        re-install.
    pydantic.ValidationError
        If the settings contain invalid types (e.g. a non-numeric
        temperature value).  Fix the ``.env`` file and restart.

    Example
    -------
    ::

        from llm_provider import get_llm
        from langchain_core.messages import HumanMessage

        llm = get_llm()
        response = await llm.ainvoke([HumanMessage(content="Hello")])
        print(response.content)
    """
    # Lazy imports — keep heavy provider deps out of module-level scope.
    # This pattern prevents import-time side effects during testing and
    # in modules that import config/schema but do not need the LLM.
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_openai import ChatOpenAI

    settings = get_settings()

    # ── Primary LLM: Google Gemini ────────────────────────────────────────────
    # ``convert_system_message_to_human=True`` is required because the Gemini
    # API does not support a dedicated "system" role in the same way as
    # OpenAI — LangChain converts it to a human-turn prefix automatically.
    primary_llm = ChatGoogleGenerativeAI(
        model=settings.PRIMARY_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=settings.LLM_TEMPERATURE,
        convert_system_message_to_human=True,
    )

    # ── Fallback LLM: OpenAI GPT-4o ──────────────────────────────────────────
    # Activated only when the primary model raises any exception.
    # No special parameters needed — OpenAI natively supports system messages.
    fallback_llm = ChatOpenAI(
        model=settings.FALLBACK_MODEL,
        openai_api_key=settings.OPENAI_API_KEY,
        temperature=settings.LLM_TEMPERATURE,
    )

    logger.info(
        "LLM provider initialised — primary=%s, fallback=%s, temperature=%.2f",
        settings.PRIMARY_MODEL,
        settings.FALLBACK_MODEL,
        settings.LLM_TEMPERATURE,
    )

    # ``with_fallbacks`` wraps the primary in a RunnableWithFallbacks.
    # On any exception from ``primary_llm``, LangChain automatically retries
    # with each fallback in the list (here, just ``fallback_llm``).
    return primary_llm.with_fallbacks([fallback_llm])

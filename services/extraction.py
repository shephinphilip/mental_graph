"""
services/extraction.py — Asynchronous Background Metadata Extraction Pipeline
===============================================================================

This module implements the post-response extraction pipeline that runs
**after** the AI reply has been delivered to the user.  It is invoked via
FastAPI ``BackgroundTasks`` so it never adds latency to the chat response.

Two extraction tasks are performed per conversation turn:

Task 1 — Insight Extraction (emotions, themes, crisis signals)
--------------------------------------------------------------
  - Calls the LLM with ``EXTRACTION_PROMPT`` to produce a
    ``SessionExtraction`` JSON object.
  - Persists the result to MongoDB ``user_insights`` collection.
  - If ``crisis_signal_detected == True``, flags the user document and
    logs a CRITICAL alert.

Task 2 — Graph Tuple Extraction (Neo4j knowledge graph)
-------------------------------------------------------
  - Calls the LLM with ``GRAPH_EXTRACTION_PROMPT`` to produce a list of
    ``GraphTuple`` (subject-predicate-object) relational facts.
  - Upserts the tuples into Neo4j via ``services/graph_rag.py``.
  - This builds the long-term "emotional memory graph" used by Graph RAG
    to enrich future conversation context.

Error isolation
---------------
Each task is wrapped in its own ``try/except`` block.  A failure in Task 1
does not prevent Task 2 from running, and vice versa.  All errors are
logged at EXCEPTION level (full traceback) but never re-raised, so a
background extraction failure is transparent to the user.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from llm_provider import get_llm
from prompts import EXTRACTION_PROMPT, GRAPH_EXTRACTION_PROMPT
from schemas import ExtractedGraphData, GraphTuple, SessionExtraction

logger = logging.getLogger(__name__)


async def run_background_extraction(
    user_id: str,
    session_id: str,
    message: str,
    reply: str,
    db: AsyncIOMotorDatabase,
    neo4j_driver=None,
) -> None:
    """
    Orchestrate post-response metadata extraction for a conversation turn.

    Designed to be called as a FastAPI ``BackgroundTask`` so it does not
    block the HTTP response.  Runs two extraction sub-tasks sequentially,
    each in its own error boundary.

    Parameters
    ----------
    user_id : str
        The unique user identifier.
    session_id : str
        The conversation session identifier.
    message : str
        The original user message text (before PII anonymization).
    reply : str
        The AI companion's reply (action card markup already stripped).
        Pass ``"[Streamed Response]"`` for SSE streaming paths where the
        full reply is not available at schedule time.
    db : AsyncIOMotorDatabase
        Motor database handle for MongoDB writes.
    neo4j_driver : neo4j.AsyncDriver, optional
        Neo4j driver for graph tuple upserts.  If ``None``, Task 2 is
        skipped silently — this is the expected behaviour in environments
        without Neo4j.

    Returns
    -------
    None
        This function always returns ``None``.  All errors are logged
        internally and never propagated.
    """
    # ── Task 1: Insight extraction (emotions, themes, crisis) ─────────────────
    # Wrapped independently so a failure here does not prevent Task 2.
    try:
        extraction = await _extract_metadata(message, reply)
        await _persist_extraction(db, user_id, session_id, extraction)

        # Trigger the crisis escalation path if the LLM detected crisis signals
        if extraction.crisis_signal_detected:
            await _handle_crisis_signal(db, user_id, session_id, extraction)

    except Exception:
        # Log the full traceback but do not re-raise — background tasks must
        # never crash the parent process or cause request failures.
        logger.exception(
            "Insight extraction failed for user=%s session=%s",
            user_id,
            session_id,
        )

    # ── Task 2: Graph tuple extraction → Neo4j ────────────────────────────────
    # Only runs if a Neo4j driver is provided (graph feature is optional).
    if neo4j_driver is not None:
        try:
            tuples = await _extract_graph_tuples(message, reply)
            if tuples:
                # Lazy import to avoid circular dependency at module load time
                from services.graph_rag import upsert_graph_tuples
                await upsert_graph_tuples(neo4j_driver, user_id, tuples)
        except Exception:
            logger.exception(
                "Graph tuple extraction failed for user=%s session=%s",
                user_id,
                session_id,
            )


# ── Insight Extraction Helpers ────────────────────────────────────────────────


async def _extract_metadata(user_message: str, ai_reply: str) -> SessionExtraction:
    """
    Call the LLM with the extraction prompt and parse the JSON response.

    Uses the ``EXTRACTION_PROMPT`` template which instructs the LLM to
    return a JSON object with emotion labels, core themes, crisis flags,
    and a one-sentence insight summary.  The raw LLM output may be
    wrapped in markdown fencing (```json ... ```) which is stripped before
    JSON parsing.

    Parameters
    ----------
    user_message : str
        The user's original message for this turn.
    ai_reply : str
        The AI companion's reply for this turn.

    Returns
    -------
    SessionExtraction
        Validated Pydantic model populated from the LLM's JSON output.

    Raises
    ------
    json.JSONDecodeError
        If the LLM returns output that is not valid JSON after stripping
        markdown fencing.
    pydantic.ValidationError
        If the LLM's JSON does not conform to the ``SessionExtraction``
        schema (e.g. missing required fields or wrong types).
    Exception
        Any LLM API error (rate limit, timeout, etc.) propagates to the
        caller (``run_background_extraction``), which handles it.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # Format the extraction prompt template with the conversation turn
    formatted_prompt = EXTRACTION_PROMPT.format(
        user_message=user_message,
        ai_reply=ai_reply,
    )

    # System instruction reinforces the JSON-only constraint
    messages = [
        SystemMessage(content="You are a precise clinical-data extraction engine. Return ONLY valid JSON."),
        HumanMessage(content=formatted_prompt),
    ]

    # Invoke with Gemini → OpenAI fallback
    llm = get_llm()
    response = await llm.ainvoke(messages)

    # Extract the text content from the LLM response object
    raw_text = response.content if hasattr(response, "content") else str(response)

    # Strip markdown fencing that some LLMs add despite instructions
    cleaned = _strip_markdown_fencing(raw_text)

    # Parse the JSON and validate against the Pydantic schema
    payload: Dict[str, Any] = json.loads(cleaned)
    extraction = SessionExtraction(**payload)

    logger.info(
        "Insight extraction complete — emotions=%s, themes=%s, crisis=%s",
        extraction.detected_emotions,
        extraction.core_themes,
        extraction.crisis_signal_detected,
    )

    return extraction


async def _persist_extraction(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
    extraction: SessionExtraction,
) -> None:
    """
    Write the extraction result to the ``user_insights`` MongoDB collection.

    Each document represents one conversation turn's worth of extracted
    metadata and is keyed by ``user_id`` + ``session_id`` + ``created_at``.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        Motor database handle.
    user_id : str
        The unique user identifier.
    session_id : str
        The conversation session identifier.
    extraction : SessionExtraction
        The validated extraction result to persist.

    Returns
    -------
    None

    Raises
    ------
    motor errors
        If the MongoDB ``insert_one`` fails (e.g. network error, write
        concern timeout).  The caller logs and swallows these.
    """
    doc = {
        "user_id": user_id,
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc),
        # ``model_dump()`` serialises all Pydantic fields to a plain dict
        **extraction.model_dump(),
    }
    await db["user_insights"].insert_one(doc)
    logger.info("Persisted extraction to user_insights for user=%s", user_id)


async def _handle_crisis_signal(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
    extraction: SessionExtraction,
) -> None:
    """
    Handle a detected crisis signal (Section 8 escalation path).

    Performs two actions:
    1. Logs a CRITICAL-level alert (visible in application monitoring
       dashboards and alerting integrations).
    2. Flags the user document in MongoDB so that human support staff,
       notification systems, or subsequent sessions can detect it.

    This function is intentionally minimal.  In a production deployment,
    extend it to:
    - Send an SMS/push notification to the user's emergency contact
    - Trigger a webhook to a clinical team's ticketing system
    - Surface an in-app banner on the user's next session open

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        Motor database handle.
    user_id : str
        The unique user identifier.
    session_id : str
        The session in which the crisis signal was detected.
    extraction : SessionExtraction
        The full extraction result (used for the ``insight_summary`` log).

    Returns
    -------
    None

    Raises
    ------
    motor errors
        If the ``update_one`` fails.  The caller logs and swallows these.
    """
    # Log at CRITICAL so this appears in alerting dashboards
    logger.critical(
        "⚠️  CRISIS SIGNAL DETECTED — user=%s session=%s — summary: %s",
        user_id,
        session_id,
        extraction.insight_summary,
    )

    # Set a crisis flag on the user document for staff visibility.
    # ``upsert=True`` ensures the user document is created if it doesn't exist.
    await db["users"].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "crisis_flag": True,
                "crisis_flagged_at": datetime.now(timezone.utc),
                "crisis_session_id": session_id,
            }
        },
        upsert=True,
    )


# ── Graph Tuple Extraction Helpers ────────────────────────────────────────────


async def _extract_graph_tuples(
    user_message: str, ai_reply: str
) -> List[GraphTuple]:
    """
    Call the LLM with the graph extraction prompt and parse the response.

    Uses the ``GRAPH_EXTRACTION_PROMPT`` template which instructs the LLM
    to extract subject-predicate-object relational facts as structured
    ``GraphTuple`` objects.  These are then upserted into Neo4j.

    Parameters
    ----------
    user_message : str
        The user's original message for this turn.
    ai_reply : str
        The AI companion's reply for this turn.

    Returns
    -------
    list[GraphTuple]
        A list of validated ``GraphTuple`` Pydantic models.  May be empty
        if the LLM found no extractable relational facts.

    Raises
    ------
    json.JSONDecodeError
        If the LLM output is not valid JSON after stripping fencing.
    pydantic.ValidationError
        If the extracted JSON does not conform to ``ExtractedGraphData``.
    Exception
        Any LLM API error propagates to the caller.
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    # Format the graph extraction prompt template
    formatted_prompt = GRAPH_EXTRACTION_PROMPT.format(
        user_message=user_message,
        ai_reply=ai_reply,
    )

    messages = [
        SystemMessage(
            content="You are a knowledge-graph extraction engine. Return ONLY valid JSON."
        ),
        HumanMessage(content=formatted_prompt),
    ]

    # Invoke with Gemini → OpenAI fallback
    llm = get_llm()
    response = await llm.ainvoke(messages)
    raw_text = response.content if hasattr(response, "content") else str(response)

    # Strip markdown fencing and parse
    cleaned = _strip_markdown_fencing(raw_text)
    payload: Dict[str, Any] = json.loads(cleaned)

    # Validate the entire payload against the ExtractedGraphData container schema
    graph_data = ExtractedGraphData(**payload)

    logger.info(
        "Graph tuple extraction complete — %d tuples extracted.",
        len(graph_data.tuples),
    )

    return graph_data.tuples


# ── Shared Utilities ──────────────────────────────────────────────────────────


def _strip_markdown_fencing(text: str) -> str:
    """
    Remove Markdown code fence wrappers that LLMs sometimes add.

    Some LLMs wrap their JSON output in ``` ... ``` or ```json ... ```
    fences despite being explicitly instructed not to.  This function
    strips those wrappers so that ``json.loads()`` can parse the result.

    Parameters
    ----------
    text : str
        The raw LLM output string, potentially containing markdown fencing.

    Returns
    -------
    str
        The cleaned string with fencing removed and whitespace stripped.

    Examples
    --------
    ::

        raw = "```json\\n{...}\\n```"
        clean = _strip_markdown_fencing(raw)
        # clean == "{...}"
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Split on newlines and filter out all lines that are fence markers
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    return cleaned

"""
services/graph.py — LangGraph Conversational State Machine
===========================================================

Implements the full conversational pipeline as a LangGraph directed graph.

Graph topology
--------------
::

    START
      │
      ▼
    fetch_context          (Node 1) — MongoDB: history, mood, habits, memory
      │
      ▼
    retrieve_graph_context (Node 2) — MongoDB: $graphLookup emotional subgraph
      │
      ▼
    generate               (Node 3) — LLM: Gemma 4 → Sarvam fallback via Bedrock
      │
      ▼
    format_output          (Node 4) — Parse cards, persist messages, build response
      │
      ▼
    END

Each node is an ``async def`` function that receives the current
``ChatState`` dictionary and returns a partial dict of updated keys.
LangGraph merges the partial updates into the shared state before passing
it to the next node.

State isolation
---------------
The ``ChatState`` TypedDict is the single source of truth flowing through
the graph.  The non-serialisable runtime object ``db`` is stored in state
as ``Any`` and is only accessed within the same process — it is never
serialised to a checkpoint.

v0.4.0 migration note
---------------------
Neo4j has been removed.  ``retrieve_graph_context_node`` now calls
``services.mongo_graph.get_user_graph_context`` via the ``db`` handle
already present in state.  The ``neo4j_driver`` key has been removed from
``ChatState`` and from ``run_chat_graph``.

Graph compilation
-----------------
The compiled graph is cached in ``_get_compiled_graph()`` using a
function-level attribute (a lightweight alternative to a module-level
global that avoids import-time side effects).  The LangGraph import is
also lazy for the same reason.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import get_settings, logger
from llm_provider import get_llm, sanitize_messages_for_bedrock
from prompts import (
    dropped_session_hint,
    format_system_prompt,
    session_phase_instructions,
)
from services.action_boundary import boundary_background, suppress_ordinary_cards
from services.action_cards import (
    attach_apm_execution_metadata,
    build_crisis_support_card,
    ensure_psychiatrist_card,
    parse_action_cards,
)
from services.apm import contains_crisis_signal
from services.meditation.cards import ensure_single_meditation_card
from services.patterns.window import evaluate_turn_risk
from services.apm import get_adaptive_memory_context
from services.chat_history import (
    decrypt_message_doc,
    find_completed_user_turn,
    load_session_messages,
    persist_user_and_assistant,
    persist_welcome_message,
)
from services.context import fetch_user_context
from services.inner_council import deliberate as inner_council_deliberate
from backend_core.security import CryptoIntegrityError


async def _record_turn_governance(db, *, user_id: str, risk_intensity: float, safety_class: str) -> dict:
    """Shadow GDS and escalation. Failures must not block the reply."""
    trajectory = "unknown"
    try:
        from services.trajectory import label_trajectory

        cursor = db["user_risk_turns"].find({"user_id": user_id}).sort("created_at", -1).limit(3)
        rows = await cursor.to_list(length=3)
        trajectory = label_trajectory(list(reversed(rows)))
    except Exception:
        trajectory = "unknown"
    try:
        from services.gds import record_shadow

        await record_shadow(db, user_id, risk_intensity=risk_intensity)
    except Exception:
        logger.info("GDS shadow skipped user=%s", user_id)
    try:
        from services.escalation import on_turn

        return await on_turn(
            db,
            user_id,
            risk_intensity=risk_intensity,
            safety_class=safety_class,
            trajectory=trajectory,
        )
    except Exception:
        logger.info("Escalation record skipped user=%s", user_id)
        return {"care_request": "", "delivery": "not_configured", "care_band": "UNMAPPED"}


# ════════════════════════════════════════════════════════════════════════════
# State Schema
# ════════════════════════════════════════════════════════════════════════════


class ChatState(TypedDict, total=False):
    """
    Shared mutable state that flows through every node in the chat graph.

    Using ``total=False`` makes all keys optional, so each node only needs
    to declare the keys it reads and writes — LangGraph merges the partial
    returns from each node into this dict.

    Input keys (set before ``ainvoke``):
    - ``user_id``       : str — unique user identifier
    - ``session_id``    : str — current conversation session identifier
    - ``user_message``  : str — the user's message for this turn
    - ``db``            : Any — Motor async database (runtime only, not serialised)
    - ``neo4j_driver``  : Any — Neo4j async driver (runtime only, not serialised)

    Keys populated by fetch_context (Node 1):
    - ``user_context``    : dict — {user_memory, recent_moods, active_habits}
    - ``message_history`` : list — [{"role": ..., "content": ...}, ...]

    Keys populated by retrieve_graph_context (Node 2):
    - ``graph_context``   : str — natural-language description of the subgraph

    Keys populated by generate (Node 3):
    - ``raw_llm_output``  : str — unprocessed LLM response (may include card markup)

    Keys populated by format_output (Node 4):
    - ``reply``           : str — clean conversational reply (markup removed)
    - ``action_cards``    : list — list of serialised ActionCard dicts
    """

    # ── Inputs ───────────────────────────────────────────────────────────────
    user_id: str
    session_id: str
    user_message: str
    db: Any          # AsyncIOMotorDatabase — runtime only
    persist_user_message: bool
    opening_turn: bool

    # ── Populated by fetch_context ────────────────────────────────────────────
    user_context: Dict[str, str]
    message_history: List[Dict[str, str]]

    # ── Populated by retrieve_graph_context ──────────────────────────────────
    graph_context: str

    # ── Populated by generate ─────────────────────────────────────────────────
    raw_llm_output: str

    # ── Populated by format_output ────────────────────────────────────────────
    reply: str
    action_cards: list
    attach_psychiatrist_card: bool
    risk_assessment: Dict[str, Any]
    meditation_offer: Dict[str, Any]


# ════════════════════════════════════════════════════════════════════════════
# Graph Nodes
# ════════════════════════════════════════════════════════════════════════════


async def fetch_context_node(state: ChatState) -> dict:
    """
    Node 1 — Aggregate MongoDB context and load conversation history.

    Reads from MongoDB collections:
    - ``messages``    : conversation history for the current session
    - ``users``       : user memory summary and key takeaways
    - ``mood_logs``   : recent mood check-ins
    - ``habit_events``: active habits

    Writes to state:
    - ``user_context``    : dict with formatted memory, moods, habits strings
    - ``message_history`` : list of {role, content} dicts (oldest first)

    Parameters
    ----------
    state : ChatState
        Must contain ``db``, ``user_id``, ``session_id``.

    Returns
    -------
    dict
        Partial state update with ``user_context`` and ``message_history``.

    Raises
    ------
    Exception
        Any MongoDB error propagates to the LangGraph engine, which will
        stop the graph and raise from ``ainvoke()``.  The caller
        (``run_chat_graph``) catches this and raises an HTTP 500.
    """
    db: AsyncIOMotorDatabase = state["db"]
    user_id = state["user_id"]
    session_id = state["session_id"]
    settings = get_settings()

    # Fetch cross-app context (memory, moods, habits) from MongoDB
    user_context = await fetch_user_context(
        db,
        user_id,
        session_id=session_id,
        user_message=state.get("user_message", ""),
        opening_turn=bool(state.get("opening_turn")),
    )
    user_context["adaptive_memory_context"] = await get_adaptive_memory_context(
        db, user_id, state.get("user_message", "")
    )

    # Load recent conversation history (stable chronological order via seq).
    history_docs = await load_session_messages(
        db,
        user_id,
        session_id,
        limit=settings.MAX_HISTORY_MESSAGES,
    )
    message_history = [decrypt_message_doc(doc) for doc in history_docs]

    user_context["dropped_session_context"] = dropped_session_hint(message_history)

    logger.info(
        "Context fetched for user=%s session=%s — %d history messages loaded",
        user_id,
        session_id,
        len(message_history),
    )

    return {"user_context": user_context, "message_history": message_history}


async def retrieve_graph_context_node(state: ChatState) -> dict:
    """
    Node 2 — Query MongoDB for the user's emotional/relational subgraph.

    Executes a user-scoped ``$graphLookup`` aggregation on the
    ``graph_relationships`` collection, traversing up to
    ``GRAPH_TRAVERSAL_DEPTH`` hops from the User root node.  Formats
    the results as natural-language bullet facts for injection into the
    system prompt.

    User isolation guarantee: every document returned is scoped to
    ``user_id`` — no cross-user data leakage is possible.

    Writes to state:
    - ``graph_context`` : str — formatted subgraph facts

    Parameters
    ----------
    state : ChatState
        Must contain ``db`` and ``user_id``.

    Returns
    -------
    dict
        Partial state update with ``graph_context``.

    Raises
    ------
    None
        All exceptions are caught internally in ``get_user_graph_context``;
        a fallback string is used if retrieval fails.
    """
    from services.mongo_graph import get_user_graph_context

    db = state["db"]
    user_id = state["user_id"]

    # get_user_graph_context handles its own errors and returns a
    # fallback string — no additional try/except needed here.
    graph_context = await get_user_graph_context(db, user_id)

    logger.info(
        "Graph context retrieved for user=%s — %d chars",
        user_id,
        len(graph_context),
    )

    return {"graph_context": graph_context}


async def generate_node(state: ChatState) -> dict:
    """
    Node 3 — Build the full prompt and invoke the LLM.

    Assembles all context sources into a structured message list:
    1. A formatted ``SystemMessage`` containing the full SYSTEM_PROMPT
       with graph context, user memory, mood logs, and habits substituted.
    2. Historical ``HumanMessage`` / ``AIMessage`` pairs from the session.
    3. The current ``HumanMessage`` with the user's new input.

    Then calls ``llm.ainvoke(messages)`` with transparent Gemini → GPT-4o
    failover (handled by ``get_llm()``).

    Reads from state:
    - ``user_context``, ``graph_context``, ``message_history``, ``user_message``

    Writes to state:
    - ``raw_llm_output`` : str — the complete, unprocessed LLM response

    Parameters
    ----------
    state : ChatState
        Must contain all context keys populated by previous nodes.

    Returns
    -------
    dict
        Partial state update with ``raw_llm_output``.

    Raises
    ------
    Exception
        LLM API errors (both Gemini and OpenAI fallback exhausted) propagate
        to the LangGraph engine and then to ``run_chat_graph()`` → HTTP 500.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    context = state.get("user_context", {})

    message_history = state.get("message_history", [])
    risk_decision = await evaluate_turn_risk(
        state["db"],
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
        message=state.get("user_message", ""),
        opening_turn=bool(state.get("opening_turn")),
    )
    council = inner_council_deliberate(
        state.get("user_message", ""),
        message_history,
        opening_turn=bool(state.get("opening_turn")),
        risk_intensity_score=risk_decision.score.risk_intensity_score,
        persistent_distress=risk_decision.persistent_distress,
        attach_psychiatrist_card=False,
        action_card_context=None,
        background_context=boundary_background(
            state.get("graph_context") or "",
            context.get("adaptive_memory_context") or "",
            context.get("pattern_context") or "",
            context.get("journal_context") or "",
            context.get("sleep_context") or "",
            context.get("task_context") or "",
        ),
    )
    stance_block = council.as_prompt_block()
    ordinary_stance = stance_block
    proactive_event_id = ""
    proactive_question = ""
    proactive_topic = ""
    proactive_language = str(context.get("preferred_language") or "ENGLISH")
    proactive_script = str(context.get("response_script") or "LATIN")
    try:
        from services.proactive.service import (
            evaluate_for_chat_turn,
            stance_with_proactive,
        )

        proactive = await evaluate_for_chat_turn(
            state["db"],
            user_id=state.get("user_id", ""),
            session_id=state.get("session_id", ""),
            user_message=state.get("user_message", ""),
            opening_turn=bool(state.get("opening_turn")),
            message_history=message_history,
            user_context=context,
            risk_intensity=risk_decision.score.risk_intensity_score,
            persistent_distress=risk_decision.persistent_distress,
            council=council,
            graph_context=state.get("graph_context") or "",
        )
        if proactive.decision == "PROACTIVE_QUESTION" and proactive.question:
            stance_block = stance_with_proactive(stance_block, proactive.question)
            proactive_event_id = proactive.event_id
            proactive_question = proactive.question
            proactive_topic = proactive.topic
            proactive_language = proactive.language
            proactive_script = proactive.script
    except Exception:
        logger.info("Proactive question skipped for user=%s", state.get("user_id", ""))
    formatted_system = format_system_prompt(
        graph_context=state.get("graph_context"),
        user_memory=context.get("user_memory"),
        recent_moods=context.get("recent_moods"),
        active_habits=context.get("active_habits"),
        dropped_session_context=context.get("dropped_session_context"),
        user_profile=context.get("user_profile"),
        academic_context=context.get("academic_context"),
        attendance_context=context.get("attendance_context"),
        assessment_context=context.get("assessment_context"),
        last_session_context=context.get("last_session_context"),
        adaptive_memory_context=context.get("adaptive_memory_context"),
        pattern_context=context.get("pattern_context"),
        sleep_context=context.get("sleep_context"),
        journal_context=context.get("journal_context"),
        task_context=context.get("task_context"),
        care_context=context.get("care_context"),
        student_profile_context=context.get("student_profile_context"),
        language_instruction=context.get("language_instruction"),
        session_phase=session_phase_instructions(
            opening_turn=bool(state.get("opening_turn")),
            message_history=message_history,
        ),
        response_stance=stance_block,
        action_card_context="",
    )

    # Assemble the full message list for the LLM
    raw_messages = [SystemMessage(content=formatted_system)]

    # Re-hydrate conversation history as typed LangChain message objects
    for msg in message_history:
        if msg["role"] == "user":
            raw_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            raw_messages.append(AIMessage(content=msg["content"]))
        # Silently skip any messages with unexpected roles

    # Append the current user turn (the message this graph invocation is responding to)
    from backend_core.security import anonymize_text

    current_user_message = state.get("user_message", "")
    if current_user_message:
        raw_messages.append(HumanMessage(content=anonymize_text(current_user_message)))
    messages = sanitize_messages_for_bedrock(raw_messages)

    from integrations.resilience import resilient_ainvoke
    from services.response_validator import (
        FALLBACK_REPLY,
        validate_reply,
    )
    from services.safety_class import SafetyClass, classify_message
    from services.stepping_stone import choose_stepping_stone

    llm = get_llm()
    response = await resilient_ainvoke(llm, messages)
    raw_output = response.content if hasattr(response, "content") else str(response)
    if not isinstance(raw_output, str):
        raw_output = str(raw_output)
    script = str(context.get("response_script") or "LATIN")
    pattern_supplied = "USER PATTERN CONTEXT" in str(context.get("pattern_context") or "")
    verdict = validate_reply(
        raw_output, script=script, pattern_supplied=pattern_supplied
    )
    used_fallback = False
    if not verdict.ok:
        from langchain_core.messages import HumanMessage as RewriteMessage

        retry_messages = messages + [
            RewriteMessage(
                content=(
                    "Rewrite the previous reply. Remove the violation "
                    f"({verdict.reason}). One question at most. "
                    "No diagnosis and no claim that a session is available."
                )
            )
        ]
        retry = await resilient_ainvoke(llm, sanitize_messages_for_bedrock(retry_messages))
        raw_output = retry.content if hasattr(retry, "content") else str(retry)
        verdict = validate_reply(
            raw_output, script=script, pattern_supplied=pattern_supplied
        )
        if not verdict.ok:
            raw_output = FALLBACK_REPLY
            used_fallback = True

    proactive_final_ok = False
    if proactive_event_id and used_fallback:
        from services.proactive.delivery import suppress_undelivered

        await suppress_undelivered(
            state["db"],
            state.get("user_id", ""),
            proactive_event_id,
            "generation_failed",
        )
    elif proactive_event_id:
        from services.proactive.delivery import (
            ensure_final_proactive_reply,
            suppress_undelivered,
        )

        async def _proactive_rewrite(instruction: str) -> str:
            from langchain_core.messages import HumanMessage as RewriteMessage

            retry = await resilient_ainvoke(
                llm,
                sanitize_messages_for_bedrock(
                    messages
                    + [RewriteMessage(content=instruction)]
                ),
            )
            text = retry.content if hasattr(retry, "content") else str(retry)
            return text if isinstance(text, str) else str(text)

        async def _ordinary_fallback(instruction: str) -> str:
            from langchain_core.messages import HumanMessage as RewriteMessage

            ordinary_system = format_system_prompt(
                graph_context=state.get("graph_context"),
                user_memory=context.get("user_memory"),
                recent_moods=context.get("recent_moods"),
                active_habits=context.get("active_habits"),
                dropped_session_context=context.get("dropped_session_context"),
                user_profile=context.get("user_profile"),
                academic_context=context.get("academic_context"),
                attendance_context=context.get("attendance_context"),
                assessment_context=context.get("assessment_context"),
                last_session_context=context.get("last_session_context"),
                adaptive_memory_context=context.get("adaptive_memory_context"),
                pattern_context=context.get("pattern_context"),
                sleep_context=context.get("sleep_context"),
                journal_context=context.get("journal_context"),
                task_context=context.get("task_context"),
                care_context=context.get("care_context"),
                student_profile_context=context.get("student_profile_context"),
                language_instruction=context.get("language_instruction"),
                session_phase=session_phase_instructions(
                    opening_turn=bool(state.get("opening_turn")),
                    message_history=message_history,
                ),
                response_stance=ordinary_stance,
                action_card_context="",
            )
            ordinary_messages = [SystemMessage(content=ordinary_system)]
            for msg in message_history:
                if msg["role"] == "user":
                    ordinary_messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    ordinary_messages.append(AIMessage(content=msg["content"]))
            if current_user_message:
                ordinary_messages.append(
                    HumanMessage(content=anonymize_text(current_user_message))
                )
            ordinary_messages.append(RewriteMessage(content=instruction))
            retry = await resilient_ainvoke(
                llm, sanitize_messages_for_bedrock(ordinary_messages)
            )
            text = retry.content if hasattr(retry, "content") else str(retry)
            return text if isinstance(text, str) else str(text)

        final_turn = await ensure_final_proactive_reply(
            raw_output,
            question=proactive_question,
            topic=proactive_topic,
            script=script,
            language=proactive_language,
            user_id=state.get("user_id", ""),
            event_id=proactive_event_id,
            pattern_supplied=pattern_supplied,
            rewrite=_proactive_rewrite,
            ordinary_fallback=_ordinary_fallback,
        )
        raw_output = final_turn.text
        proactive_final_ok = final_turn.delivered
        if not final_turn.delivered:
            await suppress_undelivered(
                state["db"],
                state.get("user_id", ""),
                proactive_event_id,
                final_turn.reason,
            )

    label = classify_message(state.get("user_message", ""))
    offer = await choose_stepping_stone(
        state["db"],
        user_id=state.get("user_id", ""),
        message=state.get("user_message", ""),
        opening_turn=bool(state.get("opening_turn")),
        safety_class=label,
    )
    governance = await _record_turn_governance(
        state["db"],
        user_id=state.get("user_id", ""),
        risk_intensity=risk_decision.score.risk_intensity_score,
        safety_class=label.value,
    )

    logger.info("LLM generation complete — %d chars", len(raw_output))

    return {
        "raw_llm_output": raw_output,
        "suppress_cards": used_fallback or label is not SafetyClass.NONE,
        "stepping_offer": offer,
        "care_request": governance.get("care_request") or "",
        "response_language": context.get("preferred_language") or "",
        "response_script": script,
        "attach_psychiatrist_card": False,
        "proactive_event_id": proactive_event_id,
        "proactive_final_ok": proactive_final_ok,
        "risk_assessment": {
            **risk_decision.score.as_dict(),
            "persistent_distress": risk_decision.persistent_distress,
            "pattern_id": risk_decision.pattern_id,
            "trigger_reason": risk_decision.trigger_reason,
        },
    }


async def format_output_node(state: ChatState) -> dict:
    """
    Node 4 — Parse action cards, persist messages, and build the response.

    Performs three operations:
    1. Parses ``<<<ACTION_CARD ... ACTION_CARD>>>`` blocks from the raw
       LLM output using ``parse_action_cards()``.
    2. Persists the user message and assistant reply to the ``messages``
       MongoDB collection.
    3. Logs any action cards emitted to the ``action_card_logs`` collection.

    Reads from state:
    - ``raw_llm_output``, ``user_message``, ``session_id``, ``user_id``, ``db``

    Writes to state:
    - ``reply``        : str — clean conversational reply (markup removed)
    - ``action_cards`` : list — serialised dicts of each ``ActionCard``

    Side effects:
    - Inserts 2 documents into ``messages`` (user + assistant)
    - Optionally inserts N documents into ``action_card_logs``

    Parameters
    ----------
    state : ChatState
        Must contain ``raw_llm_output``, ``user_message``, ``session_id``,
        ``user_id``, and ``db``.

    Returns
    -------
    dict
        Partial state update with ``reply`` and ``action_cards``.

    Raises
    ------
    Exception
        MongoDB write errors propagate to the LangGraph engine.  The caller
        (``run_chat_graph``) maps these to HTTP 500.
    """
    db: AsyncIOMotorDatabase = state["db"]
    session_id = state["session_id"]
    user_id = state["user_id"]

    # Step 1: Strip action card blocks from the raw LLM output
    clean_reply, action_cards = parse_action_cards(state["raw_llm_output"])
    if state.get("suppress_cards"):
        action_cards = []
    else:
        attach_apm_execution_metadata(action_cards, user_id)
        risk = state.get("risk_assessment") or {}
        action_cards = ensure_psychiatrist_card(
            action_cards,
            attach=bool(state.get("attach_psychiatrist_card")),
            pattern_id=risk.get("pattern_id"),
            trigger_reason=risk.get("trigger_reason") or "",
            care_request=str(state.get("care_request") or ""),
        )
        offer = state.get("stepping_offer") or {}
        action_cards = ensure_single_meditation_card(
            action_cards,
            offer.get("offer"),
            suppress=offer.get("kind") != "meditation",
        )
        action_cards = suppress_ordinary_cards(
            state.get("user_message", ""),
            action_cards,
        )

    # Step 2: Persist history as separate role-tagged records (idempotent).
    persist_result = None
    if state.get("opening_turn") and not state.get("persist_user_message", True):
        ctx = state.get("user_context") or {}
        welcome_doc, _inserted = await persist_welcome_message(
            db,
            user_id=user_id,
            session_id=session_id,
            content=clean_reply,
            response_language=str(ctx.get("preferred_language") or state.get("response_language") or ""),
            response_script=str(ctx.get("response_script") or state.get("response_script") or ""),
        )
        persist_result = {"assistant_doc": welcome_doc, "assistant_inserted": _inserted}
    else:
        persist_result = await persist_user_and_assistant(
            db,
            user_id=user_id,
            session_id=session_id,
            user_message=state["user_message"],
            assistant_reply=clean_reply,
            response_language=str(state.get("response_language") or ""),
            response_script=str(state.get("response_script") or ""),
        )

    event_id = str(state.get("proactive_event_id") or "")
    if event_id and state.get("proactive_final_ok"):
        assistant_doc = (persist_result or {}).get("assistant_doc") or {}
        message_id = str(assistant_doc.get("message_id") or "")
        if message_id:
            try:
                from services.proactive.service import mark_delivered

                await mark_delivered(
                    db,
                    user_id,
                    event_id,
                    message_id=message_id,
                    session_id=session_id,
                )
            except Exception:
                logger.info("Proactive delivered mark skipped session=%s", session_id)

    # Step 3: Log any action cards emitted in this turn for analytics/audit
    if action_cards:
        now = datetime.now(timezone.utc)
        card_docs = [
            {
                "session_id": session_id,
                "user_id": user_id,
                "card": card.model_dump(),
                "created_at": now,
            }
            for card in action_cards
        ]
        await db["action_card_logs"].insert_many(card_docs)
        logger.info(
            "Logged %d action card(s) for session=%s",
            len(action_cards),
            session_id,
        )

    return {
        "reply": clean_reply,
        "action_cards": [card.model_dump() for card in action_cards],
    }


# ════════════════════════════════════════════════════════════════════════════
# Graph Builder
# ════════════════════════════════════════════════════════════════════════════


def _build_chat_graph():
    """
    Construct and compile the conversational LangGraph state machine.

    Imports LangGraph lazily to avoid loading it at module import time
    (reduces startup cost for tests and non-graph code paths).

    Returns
    -------
    langgraph.graph.CompiledGraph
        A compiled, ready-to-invoke LangGraph state machine.

    Raises
    ------
    ImportError
        If ``langgraph`` is not installed.
    """
    from langgraph.graph import END, START, StateGraph

    # Create a graph builder configured to use the ChatState schema
    builder = StateGraph(ChatState)

    # Register all four pipeline nodes by name
    builder.add_node("fetch_context", fetch_context_node)
    builder.add_node("retrieve_graph_context", retrieve_graph_context_node)
    builder.add_node("generate", generate_node)
    builder.add_node("format_output", format_output_node)

    # Define the linear execution edges
    builder.add_edge(START, "fetch_context")
    builder.add_edge("fetch_context", "retrieve_graph_context")
    builder.add_edge("retrieve_graph_context", "generate")
    builder.add_edge("generate", "format_output")
    builder.add_edge("format_output", END)

    # Compile turns the builder into an executable, validated graph
    return builder.compile()


def _get_compiled_graph():
    """
    Lazily compile the chat graph on first use and cache it.

    Uses a function-level attribute (``_get_compiled_graph._cached``) as
    a lightweight singleton pattern.  The graph is compiled exactly once
    per process and reused across all requests — compilation is expensive
    (~100ms) due to LangGraph's internal validation.

    Returns
    -------
    langgraph.graph.CompiledGraph
        The singleton compiled graph instance.
    """
    if not hasattr(_get_compiled_graph, "_cached"):
        logger.info("Compiling LangGraph chat graph (first call)...")
        _get_compiled_graph._cached = _build_chat_graph()
        logger.info("LangGraph chat graph compiled and cached.")
    return _get_compiled_graph._cached


# ════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════


async def run_chat_graph(
    user_id: str,
    session_id: str,
    user_message: str,
    db: AsyncIOMotorDatabase,
    persist_user_message: bool = True,
    opening_turn: bool = False,
) -> Dict[str, Any]:
    """
    Execute the full LangGraph conversational pipeline for one turn.

    This is the primary public entry point called by the FastAPI
    ``/chat/send`` route handler.

    Assembles the initial ``ChatState``, invokes the compiled graph via
    ``ainvoke()``, and returns a response dict ready for
    ``ChatMessageResponse`` serialisation.

    v0.4.0: ``neo4j_driver`` parameter removed.  Graph context is now
    retrieved from MongoDB via the ``db`` handle.

    Parameters
    ----------
    user_id : str
        Unique user identifier (e.g. Firebase UID).
    session_id : str
        Unique session identifier for the current conversation.
    user_message : str
        The user's raw message text for this turn.
    db : AsyncIOMotorDatabase
        Motor async database handle (used for context, graph, persistence).

    Returns
    -------
    dict
        ``{"session_id": str, "reply": str, "action_cards": list}``

    Raises
    ------
    Exception
        Any unhandled error from within the graph nodes (MongoDB write
        failure, LLM exhaustion, etc.) propagates here.  The FastAPI
        route handler maps this to an HTTP 500 response.

    Example
    -------
    ::

        result = await run_chat_graph(
            user_id="user_001",
            session_id="session_001",
            user_message="I've been feeling really anxious today",
            db=db,
        )
        print(result["reply"])  # The AI companion's response
    """
    # Idempotent retry: if this exact user text was already answered at the
    # session tip, return the stored assistant reply without regenerating.
    if persist_user_message and not opening_turn:
        existing = await find_completed_user_turn(
            db, user_id, session_id, user_message
        )
        if existing:
            from backend_core.security import decrypt_payload

            return {
                "session_id": session_id,
                "reply": decrypt_payload(existing.get("content", "")),
                "action_cards": [],
            }

        # Acute crisis: emergency protocol now. Do not wait for the
        # 3-turn persistent-distress window or an LLM turn.
        if contains_crisis_signal(user_message):
            logger.critical(
                "Crisis keyword fast-track — user=%s session=%s",
                user_id,
                session_id,
            )
            from services.escalation import open_crisis_fast_track
            from services.language_preferences import crisis_message, resolve_response_language

            await open_crisis_fast_track(db, user_id, session_id=session_id)
            crisis_card = build_crisis_support_card()
            resolved = await resolve_response_language(db, user_id, user_message)
            crisis_reply = crisis_message(resolved["resolved_language"])
            try:
                await persist_user_and_assistant(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    user_message=user_message,
                    assistant_reply=crisis_reply,
                    response_language=resolved["resolved_language"],
                    response_script=resolved["resolved_script"],
                )
            except CryptoIntegrityError:
                raise
            except Exception:
                logger.exception(
                    "Failed to persist crisis fast-track for user=%s", user_id
                )
            return {
                "session_id": session_id,
                "reply": crisis_reply,
                "action_cards": [crisis_card.model_dump()],
            }

    # Build the initial state dict; nodes will add keys as they execute
    initial_state: ChatState = {
        "user_id": user_id,
        "session_id": session_id,
        "user_message": user_message,
        "db": db,
        "persist_user_message": persist_user_message,
        "opening_turn": opening_turn,
    }

    # Get (or compile) the cached graph and run it asynchronously
    compiled_graph = _get_compiled_graph()
    result = await compiled_graph.ainvoke(initial_state)

    return {
        "session_id": session_id,
        "reply": result["reply"],
        "action_cards": result.get("action_cards", []),
    }

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
    retrieve_graph_context (Node 2) — Neo4j: k-hop emotional subgraph
      │
      ▼
    generate               (Node 3) — LLM: Gemini → GPT-4o with full context
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
the graph.  Non-serialisable runtime objects (``db``, ``neo4j_driver``)
are stored in state as ``Any`` and are only accessed within the same
process — they are never serialised to a checkpoint.

Graph compilation
-----------------
The compiled graph is cached in ``_get_compiled_graph()`` using a
function-level attribute (a lightweight alternative to a module-level
global that avoids import-time side effects).  The LangGraph import is
also lazy for the same reason.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from llm_provider import get_llm
from prompts import SYSTEM_PROMPT
from services.action_cards import parse_action_cards
from services.context import fetch_user_context

logger = logging.getLogger(__name__)


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
    neo4j_driver: Any  # Neo4j AsyncDriver — runtime only

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
    user_context = await fetch_user_context(db, user_id)

    # Load recent conversation history for this session.
    # Sort descending (newest first), limit to MAX_HISTORY_MESSAGES, then
    # reverse so the list is oldest-first for the LLM prompt.
    cursor = (
        db["messages"]
        .find(
            {"session_id": session_id},
            {"role": 1, "content": 1, "created_at": 1},  # Project only needed fields
        )
        .sort("created_at", -1)          # Newest first for efficient limit
        .limit(settings.MAX_HISTORY_MESSAGES)
    )
    history_docs = await cursor.to_list(length=settings.MAX_HISTORY_MESSAGES)
    history_docs.reverse()  # Oldest first for chronological LLM context

    # Build a clean list of {role, content} dicts for the prompt builder
    message_history = [
        {"role": doc["role"], "content": doc["content"]}
        for doc in history_docs
    ]

    logger.info(
        "Context fetched for user=%s session=%s — %d history messages loaded",
        user_id,
        session_id,
        len(message_history),
    )

    return {"user_context": user_context, "message_history": message_history}


async def retrieve_graph_context_node(state: ChatState) -> dict:
    """
    Node 2 — Query Neo4j for the user's emotional/relational subgraph.

    Traverses up to ``GRAPH_TRAVERSAL_DEPTH`` hops from the User node
    following all therapeutic relationship types.  Formats the results as
    natural-language bullet facts for injection into the system prompt.

    Writes to state:
    - ``graph_context`` : str — formatted subgraph facts

    Design note: if the Neo4j driver is ``None`` (graph feature disabled)
    or if the query fails, a graceful fallback string is returned and the
    graph continues without interruption.

    Parameters
    ----------
    state : ChatState
        Must contain ``neo4j_driver`` (may be ``None``) and ``user_id``.

    Returns
    -------
    dict
        Partial state update with ``graph_context``.

    Raises
    ------
    None
        All exceptions are caught internally; a fallback is used instead.
    """
    from services.graph_rag import get_user_emotional_graph

    neo4j_driver = state.get("neo4j_driver")
    user_id = state["user_id"]

    # Neo4j is optional; skip gracefully if driver is not available
    if neo4j_driver is None:
        logger.warning(
            "Neo4j driver not available — skipping graph context retrieval for user=%s",
            user_id,
        )
        return {"graph_context": "No relational graph data available yet."}

    # ``get_user_emotional_graph`` handles its own errors and returns a
    # fallback string if the query fails — no additional try/except needed here.
    graph_context = await get_user_emotional_graph(neo4j_driver, user_id)

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

    # Build the system prompt by substituting all dynamic context sections.
    # The SYSTEM_PROMPT template uses {graph_context}, {user_memory},
    # {recent_moods}, and {active_habits} as named placeholders.
    formatted_system = SYSTEM_PROMPT.format(
        graph_context=state.get("graph_context", "No relational graph data available yet."),
        user_memory=context.get("user_memory", "N/A"),
        recent_moods=context.get("recent_moods", "N/A"),
        active_habits=context.get("active_habits", "N/A"),
    )

    # Assemble the full message list for the LLM
    messages = [SystemMessage(content=formatted_system)]

    # Re-hydrate conversation history as typed LangChain message objects
    for msg in state.get("message_history", []):
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            messages.append(AIMessage(content=msg["content"]))
        # Silently skip any messages with unexpected roles

    # Append the current user turn (the message this graph invocation is responding to)
    messages.append(HumanMessage(content=state["user_message"]))

    # Invoke the LLM chain; Gemini → OpenAI failover is transparent here
    llm = get_llm()
    response = await llm.ainvoke(messages)

    # Extract the text content; handle both message objects and raw strings
    raw_output = response.content if hasattr(response, "content") else str(response)

    logger.info("LLM generation complete — %d chars", len(raw_output))

    return {"raw_llm_output": raw_output}


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
    # Single timestamp for both message inserts keeps them consistent
    now = datetime.now(timezone.utc)

    # Step 1: Strip action card blocks from the raw LLM output
    # ``parse_action_cards`` handles malformed blocks gracefully (logs + skips)
    clean_reply, action_cards = parse_action_cards(state["raw_llm_output"])

    # Step 2: Persist the user message to MongoDB
    await db["messages"].insert_one(
        {
            "session_id": session_id,
            "user_id": user_id,
            "role": "user",
            "content": state["user_message"],
            "created_at": now,
        }
    )

    # Step 3: Persist the assistant reply to MongoDB
    await db["messages"].insert_one(
        {
            "session_id": session_id,
            "user_id": user_id,
            "role": "assistant",
            "content": clean_reply,
            "created_at": now,
        }
    )

    # Step 4: Log any action cards emitted in this turn for analytics/audit
    if action_cards:
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
        # Serialise to plain dicts so the response is JSON-serialisable
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
    neo4j_driver=None,
) -> Dict[str, Any]:
    """
    Execute the full LangGraph conversational pipeline for one turn.

    This is the primary public entry point called by the FastAPI
    ``/chat/send`` route handler.

    Assembles the initial ``ChatState``, invokes the compiled graph via
    ``ainvoke()``, and returns a response dict ready for
    ``ChatMessageResponse`` serialisation.

    Parameters
    ----------
    user_id : str
        Unique user identifier (e.g. Firebase UID).
    session_id : str
        Unique session identifier for the current conversation.
    user_message : str
        The user's raw message text for this turn.
    db : AsyncIOMotorDatabase
        Motor async database handle.
    neo4j_driver : neo4j.AsyncDriver, optional
        Neo4j async driver.  If ``None``, graph context retrieval is
        skipped and the graph continues with a fallback string.

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
            neo4j_driver=driver,
        )
        print(result["reply"])  # The AI companion's response
    """
    # Build the initial state dict; nodes will add keys as they execute
    initial_state: ChatState = {
        "user_id": user_id,
        "session_id": session_id,
        "user_message": user_message,
        "db": db,
        "neo4j_driver": neo4j_driver,
    }

    # Get (or compile) the cached graph and run it asynchronously
    compiled_graph = _get_compiled_graph()
    result = await compiled_graph.ainvoke(initial_state)

    return {
        "session_id": session_id,
        "reply": result["reply"],
        "action_cards": result.get("action_cards", []),
    }

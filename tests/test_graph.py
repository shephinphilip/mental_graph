"""
Integration tests for the LangGraph conversational state machine.

Uses mocked MongoDB, mocked Neo4j, and mocked LLM to verify the full
graph flow:
    fetch_context → retrieve_graph_context → generate → format_output
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.graph import run_chat_graph


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_mock_db(
    user_doc=None,
    mood_docs=None,
    habit_docs=None,
    message_docs=None,
):
    """
    Build a mock AsyncIOMotorDatabase that returns controlled data from
    find / find_one calls on the relevant collections.
    """
    db = MagicMock()

    # users.find_one
    db["users"].find_one = AsyncMock(return_value=user_doc)

    # mood_logs.find → async cursor
    mood_cursor = AsyncMock()
    mood_cursor.sort = MagicMock(return_value=mood_cursor)
    mood_cursor.__aiter__ = MagicMock(return_value=iter(mood_docs or []))
    db["mood_logs"].find = MagicMock(return_value=mood_cursor)

    # habit_events.find → async cursor
    habit_cursor = AsyncMock()
    habit_cursor.__aiter__ = MagicMock(return_value=iter(habit_docs or []))
    db["habit_events"].find = MagicMock(return_value=habit_cursor)

    # messages.find → async cursor with sort + limit + to_list
    msg_cursor = MagicMock()
    msg_cursor.sort = MagicMock(return_value=msg_cursor)
    msg_cursor.limit = MagicMock(return_value=msg_cursor)
    msg_cursor.to_list = AsyncMock(return_value=message_docs or [])
    db["messages"].find = MagicMock(return_value=msg_cursor)

    # Writes
    db["messages"].insert_one = AsyncMock()
    db["action_card_logs"].insert_many = AsyncMock()

    return db


def _mock_llm_response(content: str):
    """Create a mock LLM response object with a .content attribute."""
    response = MagicMock()
    response.content = content
    return response


# ── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_flow_no_action_cards():
    """A simple message with no action cards should return a clean reply."""
    mock_db = _make_mock_db(
        user_doc={"memory_summary": "User has been stressed about exams."},
    )

    llm_reply = "I hear you — exam stress can feel overwhelming. Tell me more about what's been weighing on you."

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_mock_llm_response(llm_reply))
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_001",
            session_id="sess_001",
            user_message="I'm so stressed about my finals.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    assert result["session_id"] == "sess_001"
    assert result["reply"] == llm_reply
    assert result["action_cards"] == []
    # Verify two messages persisted (user + assistant)
    assert mock_db["messages"].insert_one.call_count == 2


@pytest.mark.asyncio
async def test_flow_with_action_card():
    """When the LLM emits an action card, it should be parsed and returned."""
    mock_db = _make_mock_db()

    llm_reply = (
        "A breathing exercise might help right now.\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "TOOL_CARD", "title": "4-7-8 Breathing", '
        '"subtitle": "Calming breath technique", '
        '"action_payload": {"resource_id": "breathe_478"}}\n'
        'ACTION_CARD>>>'
    )

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=_mock_llm_response(llm_reply))
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_002",
            session_id="sess_002",
            user_message="I can't stop my mind from racing.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    assert result["reply"] == "A breathing exercise might help right now."
    assert len(result["action_cards"]) == 1
    assert result["action_cards"][0]["card_type"] == "TOOL_CARD"
    assert result["action_cards"][0]["action_payload"]["resource_id"] == "breathe_478"
    # Action cards should have been logged
    mock_db["action_card_logs"].insert_many.assert_called_once()


@pytest.mark.asyncio
async def test_conversation_history_loaded():
    """
    Verify that existing conversation history is loaded from the messages
    collection and fed into the LLM context.
    """
    history = [
        {"role": "user", "content": "I've been feeling down lately."},
        {"role": "assistant", "content": "I'm sorry to hear that. What's been going on?"},
    ]
    mock_db = _make_mock_db(message_docs=history)

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=_mock_llm_response("That sounds tough. Let's talk more about it.")
        )
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_003",
            session_id="sess_003",
            user_message="It's my relationship, mostly.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    # The LLM should have been called with messages including history
    call_args = mock_llm.ainvoke.call_args[0][0]
    # System + 2 history + 1 current = 4 messages
    assert len(call_args) == 4
    assert result["reply"] == "That sounds tough. Let's talk more about it."


@pytest.mark.asyncio
async def test_empty_context_handled():
    """Graph should work even when the user has no prior data."""
    mock_db = _make_mock_db()

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=_mock_llm_response("Welcome! I'm here to listen.")
        )
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="new_user",
            session_id="sess_new",
            user_message="Hello",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    assert result["reply"] == "Welcome! I'm here to listen."
    assert result["action_cards"] == []


@pytest.mark.asyncio
async def test_graph_context_in_prompt():
    """
    Verify that graph context from Neo4j is included in the system prompt
    sent to the LLM.
    """
    mock_db = _make_mock_db()
    graph_facts = "• Emotional state: Anxiety (intensity: 7) — via: experiences\n• Trigger: Work Deadlines"

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = graph_facts
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=_mock_llm_response("Work deadlines can be a real weight. How are you managing right now?")
        )
        mock_get_llm.return_value = mock_llm

        await run_chat_graph(
            user_id="user_graph",
            session_id="sess_graph",
            user_message="I'm anxious again.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    # Check the system message contains the graph facts
    call_args = mock_llm.ainvoke.call_args[0][0]
    system_message_content = call_args[0].content
    assert "Anxiety (intensity: 7)" in system_message_content
    assert "Work Deadlines" in system_message_content


@pytest.mark.asyncio
async def test_neo4j_driver_none_graceful():
    """When neo4j_driver is None, graph context should fall back gracefully."""
    mock_db = _make_mock_db()

    with patch("services.graph.get_llm") as mock_get_llm:
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(
            return_value=_mock_llm_response("Hey there. What's on your mind?")
        )
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_no_neo4j",
            session_id="sess_no_neo4j",
            user_message="Hi",
            db=mock_db,
            neo4j_driver=None,  # No Neo4j available
        )

    assert result["reply"] == "Hey there. What's on your mind?"
    # System prompt should contain the fallback text
    call_args = mock_llm.ainvoke.call_args[0][0]
    system_content = call_args[0].content
    assert "No relational graph data available yet." in system_content

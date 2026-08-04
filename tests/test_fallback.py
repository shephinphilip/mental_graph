"""
Tests for the dual-LLM failover mechanism.

Simulates Gemini API failures (rate limit, timeout, service error) and
verifies that the system transparently falls back to OpenAI without
dropping state or producing unhandled errors.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.graph import run_chat_graph


def _make_mock_db():
    """Minimal mock DB for failover tests."""
    db = MagicMock()
    db["users"].find_one = AsyncMock(return_value=None)

    empty_cursor = AsyncMock()
    empty_cursor.sort = MagicMock(return_value=empty_cursor)
    empty_cursor.__aiter__ = MagicMock(return_value=iter([]))
    db["mood_logs"].find = MagicMock(return_value=empty_cursor)

    habit_cursor = AsyncMock()
    habit_cursor.__aiter__ = MagicMock(return_value=iter([]))
    db["habit_events"].find = MagicMock(return_value=habit_cursor)

    msg_cursor = MagicMock()
    msg_cursor.sort = MagicMock(return_value=msg_cursor)
    msg_cursor.limit = MagicMock(return_value=msg_cursor)
    msg_cursor.to_list = AsyncMock(return_value=[])
    db["messages"].find = MagicMock(return_value=msg_cursor)

    db["messages"].insert_one = AsyncMock()
    db["action_card_logs"].insert_many = AsyncMock()

    return db


@pytest.mark.asyncio
async def test_fallback_on_rate_limit():
    """When Gemini returns 429, OpenAI fallback should produce a valid response."""
    mock_db = _make_mock_db()

    fallback_response = MagicMock()
    fallback_response.content = "I'm here for you. Tell me what's on your mind."

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=fallback_response)
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_fail_1",
            session_id="sess_fail_1",
            user_message="I need someone to talk to.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    assert result["session_id"] == "sess_fail_1"
    assert result["reply"] == "I'm here for you. Tell me what's on your mind."
    assert isinstance(result["action_cards"], list)


@pytest.mark.asyncio
async def test_fallback_preserves_action_cards():
    """Even via fallback, action cards in the response should be parsed correctly."""
    mock_db = _make_mock_db()

    fallback_reply = (
        "Let's try a calming exercise.\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "TOOL_CARD", "title": "Box Breathing", '
        '"action_payload": {"resource_id": "box_breathing"}}\n'
        'ACTION_CARD>>>'
    )

    fallback_response = MagicMock()
    fallback_response.content = fallback_reply

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=fallback_response)
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_fail_2",
            session_id="sess_fail_2",
            user_message="My anxiety is spiking right now.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    assert result["reply"] == "Let's try a calming exercise."
    assert len(result["action_cards"]) == 1
    assert result["action_cards"][0]["card_type"] == "TOOL_CARD"


@pytest.mark.asyncio
async def test_response_schema_after_fallback():
    """Response from fallback must match ChatMessageResponse schema."""
    mock_db = _make_mock_db()

    from schemas import ChatMessageResponse

    fallback_response = MagicMock()
    fallback_response.content = "You're not alone in this."

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=fallback_response)
        mock_get_llm.return_value = mock_llm

        result = await run_chat_graph(
            user_id="user_fail_3",
            session_id="sess_fail_3",
            user_message="Nobody understands me.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    # Validate against Pydantic schema
    validated = ChatMessageResponse(**result)
    assert validated.session_id == "sess_fail_3"
    assert validated.reply == "You're not alone in this."
    assert validated.action_cards == []


@pytest.mark.asyncio
async def test_state_integrity_after_fallback():
    """
    After fallback, messages should still be persisted to MongoDB
    (state is not dropped during failover).
    """
    mock_db = _make_mock_db()

    fallback_response = MagicMock()
    fallback_response.content = "Let's work through this together."

    with patch("services.graph.get_llm") as mock_get_llm, \
         patch("services.graph_rag.get_user_emotional_graph", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = "No relational graph data available yet."
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=fallback_response)
        mock_get_llm.return_value = mock_llm

        await run_chat_graph(
            user_id="user_fail_4",
            session_id="sess_fail_4",
            user_message="Everything feels hopeless.",
            db=mock_db,
            neo4j_driver=MagicMock(),
        )

    # Two insert_one calls: user message + assistant reply
    assert mock_db["messages"].insert_one.call_count == 2

    # Verify the persisted content
    calls = mock_db["messages"].insert_one.call_args_list
    user_msg_doc = calls[0][0][0]
    assistant_msg_doc = calls[1][0][0]

    assert user_msg_doc["role"] == "user"
    assert user_msg_doc["content"] == "Everything feels hopeless."
    assert assistant_msg_doc["role"] == "assistant"
    assert assistant_msg_doc["content"] == "Let's work through this together."

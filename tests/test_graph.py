"""
Integration tests for the LangGraph conversational state machine.

Uses mocked MongoDB and mocked LLM to verify the full graph flow:
    fetch_context → retrieve_graph_context → generate → format_output
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.graph import run_chat_graph


def _make_mock_db(
    user_doc=None,
    mood_docs=None,
    habit_docs=None,
    message_docs=None,
):
    db = MagicMock()

    db["users"].find_one = AsyncMock(return_value=user_doc)

    mood_cursor = AsyncMock()
    mood_cursor.sort = MagicMock(return_value=mood_cursor)
    mood_cursor.__aiter__ = MagicMock(return_value=iter(mood_docs or []))
    db["mood_logs"].find = MagicMock(return_value=mood_cursor)

    habit_cursor = AsyncMock()
    habit_cursor.__aiter__ = MagicMock(return_value=iter(habit_docs or []))
    db["habit_events"].find = MagicMock(return_value=habit_cursor)

    msg_cursor = MagicMock()
    msg_cursor.sort = MagicMock(return_value=msg_cursor)
    msg_cursor.limit = MagicMock(return_value=msg_cursor)
    msg_cursor.to_list = AsyncMock(return_value=message_docs or [])
    db["messages"].find = MagicMock(return_value=msg_cursor)
    db["messages"].find_one = AsyncMock(return_value=None)

    db["messages"].insert_one = AsyncMock()
    db["action_card_logs"].insert_many = AsyncMock()

    return db


def _mock_llm_response(content: str):
    response = MagicMock()
    response.content = content
    return response


@pytest.mark.asyncio
async def test_basic_flow_no_action_cards():
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
        )

    assert result["session_id"] == "sess_001"
    assert result["reply"] == llm_reply
    assert result["action_cards"] == []
    assert mock_db["messages"].insert_one.call_count == 2


@pytest.mark.asyncio
async def test_flow_with_action_card():
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
        )

    assert result["reply"] == "A breathing exercise might help right now."
    assert len(result["action_cards"]) == 1
    assert result["action_cards"][0]["card_type"] == "TOOL_CARD"
    assert result["action_cards"][0]["action_payload"]["resource_id"] == "breathe_478"
    mock_db["action_card_logs"].insert_many.assert_called_once()

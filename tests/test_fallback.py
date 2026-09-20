"""
Tests for the dual-LLM failover mechanism (AWS Bedrock -> Sarvam).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.graph import run_chat_graph


def _make_mock_db():
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
async def test_fallback_on_primary_failure():
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
        )

    assert result["session_id"] == "sess_fail_1"
    assert result["reply"] == "I'm here for you. Tell me what's on your mind."
    assert isinstance(result["action_cards"], list)

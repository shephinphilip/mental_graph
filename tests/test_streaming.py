"""
Tests for Server-Sent Events (SSE) streaming engine.

Covers:
    - Real-time token streaming format (event: token \n data: ...)
    - Immediate Section 8 Crisis intervention safeguard trigger
    - Pre-first-token streaming fallback
    - Mid-stream failure termination (no stitching)
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.streaming import stream_chat_graph


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
    db["messages"].find_one = AsyncMock(return_value=None)

    db["messages"].insert_one = AsyncMock()
    db["action_card_logs"].insert_many = AsyncMock()

    return db


@pytest.mark.asyncio
async def test_streaming_crisis_trigger():
    """Immediate crisis trigger must yield crisis_alert event with emergency helplines."""
    mock_db = _make_mock_db()

    events = []
    async for sse_event in stream_chat_graph(
        user_id="user_crisis",
        session_id="sess_crisis",
        user_message="I feel hopeless and want to end my life",
        db=mock_db,
    ):
        events.append(sse_event)

    full_output = "".join(events)
    assert "event: crisis_alert" in full_output
    assert "Tele-MANAS" in full_output
    assert "Vandrevala Foundation" in full_output
    assert "event: done" in full_output


@pytest.mark.asyncio
async def test_streaming_normal_tokens():
    """Normal message streaming should yield token events and completion marker."""
    mock_db = _make_mock_db()

    async def mock_primary_astream(messages):
        yield MagicMock(content="Hello ")
        yield MagicMock(content="there!")

    with patch("services.streaming.get_primary_llm") as mock_get_primary:
        mock_llm = MagicMock()
        mock_llm.astream = mock_primary_astream
        mock_get_primary.return_value = mock_llm

        events = []
        async for sse_event in stream_chat_graph(
            user_id="user_stream",
            session_id="sess_stream",
            user_message="Hi there companion",
            db=mock_db,
        ):
            events.append(sse_event)

    full_output = "".join(events)
    assert "event: token" in full_output
    assert "Hello" in full_output
    assert "there!" in full_output
    assert "event: done" in full_output


@pytest.mark.asyncio
async def test_streaming_fallback_before_first_token():
    """When primary LLM fails before first token, fallback LLM takes over."""
    mock_db = _make_mock_db()

    async def failing_primary(messages):
        if False:
            yield None
        raise RuntimeError("Primary Bedrock error before first token")

    async def fallback_astream(messages):
        yield MagicMock(content="Fallback response token")

    with patch("services.streaming.get_primary_llm") as mock_get_primary, \
         patch("services.streaming.get_fallback_llm") as mock_get_fallback:

        mock_primary = MagicMock()
        mock_primary.astream = failing_primary
        mock_get_primary.return_value = mock_primary

        mock_fallback = MagicMock()
        mock_fallback.astream = fallback_astream
        mock_get_fallback.return_value = mock_fallback

        events = []
        async for sse_event in stream_chat_graph(
            user_id="user_stream_fallback",
            session_id="sess_stream_fallback",
            user_message="Test fallback",
            db=mock_db,
        ):
            events.append(sse_event)

    full_output = "".join(events)
    mock_get_fallback.assert_called_once_with()
    assert "Fallback response token" in full_output
    assert "event: done" in full_output


@pytest.mark.asyncio
async def test_empty_primary_chunks_do_not_block_pre_token_fallback():
    """An empty provider chunk is not a client-visible first token."""
    mock_db = _make_mock_db()

    async def primary_with_empty_chunk(messages):
        yield MagicMock(content="")
        raise RuntimeError("Primary failed before visible output")

    async def fallback_astream(messages):
        yield MagicMock(content="Sarvam starts cleanly")

    with patch("services.streaming.get_primary_llm") as mock_get_primary, \
         patch("services.streaming.get_fallback_llm") as mock_get_fallback:
        primary = MagicMock()
        primary.astream = primary_with_empty_chunk
        mock_get_primary.return_value = primary
        fallback = MagicMock()
        fallback.astream = fallback_astream
        mock_get_fallback.return_value = fallback

        events = [
            event
            async for event in stream_chat_graph(
                user_id="user_empty_chunk",
                session_id="sess_empty_chunk",
                user_message="Test empty chunk",
                db=mock_db,
            )
        ]

    mock_get_fallback.assert_called_once_with()
    output = "".join(events)
    assert "Sarvam starts cleanly" in output
    assert "event: done" in output


@pytest.mark.asyncio
async def test_streaming_mid_stream_failure_terminates():
    """Mid-stream failure after >=1 token must emit error event and terminate without stitching."""
    mock_db = _make_mock_db()

    async def midstream_failing_primary(messages):
        yield MagicMock(content="First token ")
        raise RuntimeError("Network failure mid-stream!")

    with patch("services.streaming.get_primary_llm") as mock_get_primary, \
         patch("services.streaming.get_fallback_llm") as mock_get_fallback:

        mock_primary = MagicMock()
        mock_primary.astream = midstream_failing_primary
        mock_get_primary.return_value = mock_primary

        events = []
        async for sse_event in stream_chat_graph(
            user_id="user_midstream_fail",
            session_id="sess_midstream_fail",
            user_message="Test midstream failure",
            db=mock_db,
        ):
            events.append(sse_event)

    mock_get_fallback.assert_not_called()
    full_output = "".join(events)
    assert "First token" in full_output
    assert "event: error" in full_output
    assert "Response stream interrupted" in full_output
    assert "event: done" not in full_output
    assert mock_db["messages"].insert_one.await_count == 0

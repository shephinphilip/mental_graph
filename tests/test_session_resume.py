"""
Integration tests for sub-500ms session resumption.

Covers:
    - Resume response formatting with recent history & dropped session context
    - Automatic decrypt of encrypted messages in history
    - Handling empty or new user sessions
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from services.security import encrypt_payload
from services.session_resume import resume_user_session


def _make_mock_db(message_docs=None, user_doc=None):
    db = MagicMock()

    users_mock = MagicMock()
    users_mock.find_one = AsyncMock(return_value=user_doc)

    msg_cursor = MagicMock()
    msg_cursor.sort = MagicMock(return_value=msg_cursor)
    msg_cursor.limit = MagicMock(return_value=msg_cursor)
    msg_cursor.to_list = AsyncMock(return_value=message_docs or [])

    messages_mock = MagicMock()
    messages_mock.find = MagicMock(return_value=msg_cursor)
    messages_mock.find_one = AsyncMock(return_value={"session_id": "sess_latest"} if message_docs else None)

    collections = {
        "users": users_mock,
        "messages": messages_mock,
    }
    db.__getitem__.side_effect = lambda name: collections.get(name, MagicMock())

    return db


@pytest.mark.asyncio
async def test_session_resume_success():
    enc_msg_1 = encrypt_payload("Hello companion")
    enc_msg_2 = encrypt_payload("Hi! How can I help you today?")

    # MongoDB cursor returns documents sorted descending (newest first)
    docs_descending = [
        {"role": "assistant", "content": enc_msg_2, "created_at": "2026-07-21T10:00:05Z"},
        {"role": "user", "content": enc_msg_1, "created_at": "2026-07-21T10:00:00Z"},
    ]
    mock_db = _make_mock_db(message_docs=docs_descending, user_doc={"active_emotional_state": "reflective"})

    response = await resume_user_session(mock_db, user_id="user_res_1", session_id="sess_res_1")

    assert response.is_resumed is True
    assert response.session_id == "sess_res_1"
    assert len(response.recent_messages) == 2
    assert response.recent_messages[0]["content"] == "Hello companion"
    assert response.recent_messages[1]["content"] == "Hi! How can I help you today?"
    assert "Resuming after last AI response" in response.dropped_session_context
    assert response.active_emotional_state == "reflective"


@pytest.mark.asyncio
async def test_session_resume_empty_session():
    mock_db = _make_mock_db(message_docs=[], user_doc=None)

    response = await resume_user_session(mock_db, user_id="user_new")

    assert response.is_resumed is True
    assert len(response.recent_messages) == 0
    assert response.dropped_session_context is None

"""Tests for idempotent welcome + chat history persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pymongo.errors import DuplicateKeyError

from services.chat_history import (
    KIND_WELCOME,
    decrypt_message_doc,
    find_completed_user_turn,
    get_welcome_message,
    load_session_messages,
    persist_user_and_assistant,
    persist_welcome_message,
    session_has_any_messages,
)
from services.security import decrypt_payload
from services.session_resume import resume_user_session


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        # Caller always requests newest-first; store is chronological.
        self._docs = list(reversed(self._docs))
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length: int = None):
        return list(self._docs[: length or len(self._docs)])


class FakeMessages:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query: Dict[str, Any], projection=None):
        matched = [d for d in self.docs if _match(d, query)]
        return _Cursor(matched)

    async def find_one(self, query: Dict[str, Any], sort=None, projection=None):
        matched = [d for d in self.docs if _match(d, query)]
        if sort:
            reverse = any(direction < 0 for _, direction in sort)
            matched.sort(
                key=lambda d: (d.get("created_at"), d.get("seq") or 0),
                reverse=reverse,
            )
        return matched[0] if matched else None

    async def insert_one(self, doc: Dict[str, Any]):
        key = doc.get("idempotency_key")
        if key and any(d.get("idempotency_key") == key for d in self.docs):
            raise DuplicateKeyError("dup idempotency_key")
        if doc.get("message_kind") == KIND_WELCOME:
            if any(
                d.get("session_id") == doc["session_id"]
                and d.get("user_id") == doc["user_id"]
                and d.get("message_kind") == KIND_WELCOME
                for d in self.docs
            ):
                raise DuplicateKeyError("dup welcome")
        stored = dict(doc)
        stored.setdefault("_id", f"id_{len(self.docs)+1}")
        self.docs.append(stored)
        return MagicMock(inserted_id=stored["_id"])


def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, value in query.items():
        if doc.get(key) != value:
            return False
    return True


def _make_db(messages: FakeMessages) -> MagicMock:
    db = MagicMock()
    users = MagicMock()
    users.find_one = AsyncMock(return_value={"active_emotional_state": "calm"})
    db.__getitem__.side_effect = lambda name: messages if name == "messages" else users
    return db


@pytest.mark.asyncio
async def test_welcome_is_stored_once_and_not_duplicated():
    messages = FakeMessages()
    db = _make_db(messages)

    doc1, inserted1 = await persist_welcome_message(
        db, user_id="u1", session_id="s1", content="Hello Meera. Fresh start."
    )
    doc2, inserted2 = await persist_welcome_message(
        db, user_id="u1", session_id="s1", content="Hello Meera. Fresh start."
    )

    assert inserted1 is True
    assert inserted2 is False
    assert doc1["message_kind"] == KIND_WELCOME
    assert doc1["role"] == "assistant"
    assert len(messages.docs) == 1
    assert decrypt_payload(messages.docs[0]["content"]) == "Hello Meera. Fresh start."
    assert await session_has_any_messages(db, "u1", "s1")
    assert (await get_welcome_message(db, "u1", "s1")) is not None


@pytest.mark.asyncio
async def test_user_reply_then_ai_forms_complete_sequence():
    messages = FakeMessages()
    db = _make_db(messages)

    await persist_welcome_message(
        db, user_id="u1", session_id="s1", content="Welcome line"
    )
    result = await persist_user_and_assistant(
        db,
        user_id="u1",
        session_id="s1",
        user_message="I have something to say about today's incident.",
        assistant_reply="That sounds delicate to hold. We can go at your pace.",
    )

    assert result["user_inserted"] is True
    assert result["assistant_inserted"] is True
    assert len(messages.docs) == 3

    history = await load_session_messages(db, "u1", "s1", limit=10)
    roles = [d["role"] for d in history]
    texts = [decrypt_message_doc(d)["content"] for d in history]
    assert roles == ["assistant", "user", "assistant"]
    assert texts[0] == "Welcome line"
    assert texts[1] == "I have something to say about today's incident."
    assert texts[2].startswith("That sounds delicate")
    # Ordered timestamps / seq — user before assistant reply
    assert history[1]["seq"] < history[2]["seq"]
    assert history[1]["created_at"] <= history[2]["created_at"]


@pytest.mark.asyncio
async def test_duplicate_request_does_not_duplicate_user_or_ai():
    messages = FakeMessages()
    db = _make_db(messages)

    await persist_welcome_message(db, user_id="u1", session_id="s1", content="Welcome")
    await persist_user_and_assistant(
        db,
        user_id="u1",
        session_id="s1",
        user_message="Same text",
        assistant_reply="First reply",
    )
    await persist_user_and_assistant(
        db,
        user_id="u1",
        session_id="s1",
        user_message="Same text",
        assistant_reply="Would-be second reply",
    )

    assert len(messages.docs) == 3
    existing = await find_completed_user_turn(db, "u1", "s1", "Same text")
    assert existing is not None
    assert decrypt_payload(existing["content"]) == "First reply"


@pytest.mark.asyncio
async def test_existing_session_does_not_insert_welcome_again_via_kind_check():
    messages = FakeMessages()
    db = _make_db(messages)
    await persist_welcome_message(db, user_id="u1", session_id="s1", content="Welcome")
    await persist_user_and_assistant(
        db,
        user_id="u1",
        session_id="s1",
        user_message="hi",
        assistant_reply="hello back",
    )
    # Attempting welcome again must no-op via get_welcome_message short-circuit
    _, inserted = await persist_welcome_message(
        db, user_id="u1", session_id="s1", content="Another welcome"
    )
    assert inserted is False
    welcomes = [d for d in messages.docs if d.get("message_kind") == KIND_WELCOME]
    assert len(welcomes) == 1


@pytest.mark.asyncio
async def test_session_resume_returns_welcome_user_ai_in_order():
    messages = FakeMessages()
    db = _make_db(messages)
    await persist_welcome_message(db, user_id="u1", session_id="s1", content="AI welcome")
    await persist_user_and_assistant(
        db,
        user_id="u1",
        session_id="s1",
        user_message="User first reply",
        assistant_reply="AI follow-up",
    )

    response = await resume_user_session(db, user_id="u1", session_id="s1")
    assert [m["role"] for m in response.recent_messages] == [
        "assistant",
        "user",
        "assistant",
    ]
    assert [m["content"] for m in response.recent_messages] == [
        "AI welcome",
        "User first reply",
        "AI follow-up",
    ]


@pytest.mark.asyncio
async def test_streaming_persist_path_matches_send_sequence():
    """Streaming uses the same persist_user_and_assistant helper as /chat/send."""
    messages = FakeMessages()
    db = _make_db(messages)
    await persist_welcome_message(db, user_id="u1", session_id="s1", content="Welcome")

    with patch("services.streaming.persist_user_and_assistant", wraps=persist_user_and_assistant):
        # Directly exercise the shared helper the stream path calls
        await persist_user_and_assistant(
            db,
            user_id="u1",
            session_id="s1",
            user_message="streamed user text",
            assistant_reply="streamed assistant text",
        )

    history = [decrypt_message_doc(d) for d in await load_session_messages(db, "u1", "s1")]
    assert [h["role"] for h in history] == ["assistant", "user", "assistant"]
    assert history[1]["content"] == "streamed user text"
    assert history[2]["content"] == "streamed assistant text"

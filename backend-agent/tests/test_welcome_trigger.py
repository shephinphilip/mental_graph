"""POST /chat/send treats WELCOME MESSAGE as an internal opening command."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from database import get_db
from prompts import WELCOME_USER_CUE
from reports.concerns import build_key_concerns
from backend_core.security import decrypt_payload
from backend_core.users import issue_access_token
from services.welcome import WELCOME_TRIGGER, is_welcome_trigger
from tests.test_tracking import _db


def _auth(user_id: str = "user_a"):
    return {"Authorization": "Bearer " + issue_access_token(user_id)}


def _client(fake):
    app.dependency_overrides[get_db] = lambda: fake
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    stack = patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    )
    return stack


def _welcome_graph(calls):
    async def _run(**kwargs):
        calls.append(kwargs)
        from services.chat_history import persist_welcome_message

        await persist_welcome_message(
            kwargs["db"],
            user_id=kwargs["user_id"],
            session_id=kwargs["session_id"],
            content="Hello Meera. How are you feeling today?",
            response_language="ENGLISH",
            response_script="LATIN",
        )
        return {
            "session_id": kwargs["session_id"],
            "reply": "Hello Meera. How are you feeling today?",
            "action_cards": [],
        }

    return _run


def test_normal_chat_still_uses_the_chat_pipeline():
    fake = _db()
    graph = AsyncMock(
        return_value={"session_id": "sess_123", "reply": "I hear you.", "action_cards": []}
    )
    extraction = AsyncMock()
    patches = _client(fake)
    with patches[0], patches[1], patch(
        "api.routes.chat.run_chat_graph", graph
    ), patch("api.routes.chat.run_background_extraction", extraction):
        client = TestClient(app)
        response = client.post(
            "/chat/send",
            headers=_auth(),
            json={
                "user_id": "user_a",
                "session_id": "sess_123",
                "message": "I feel stressed about my exams.",
            },
        )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["reply"] == "I hear you."
    assert graph.await_args.kwargs["user_message"] == "I feel stressed about my exams."
    assert graph.await_args.kwargs.get("opening_turn", False) is False
    extraction.assert_awaited()


def test_welcome_trigger_uses_the_welcome_flow_only():
    fake = _db()
    calls = []
    extraction = AsyncMock()
    patches = _client(fake)
    with patches[0], patches[1], patch(
        "services.graph.run_chat_graph", side_effect=_welcome_graph(calls)
    ), patch("api.routes.chat.run_background_extraction", extraction):
        client = TestClient(app)
        response = client.post(
            "/chat/send",
            headers=_auth(),
            json={
                "user_id": "user_a",
                "session_id": "sess_123",
                "message": WELCOME_TRIGGER,
            },
        )
    app.dependency_overrides.clear()
    body = response.json()
    assert response.status_code == 200
    assert body["reply"] == "Hello Meera. How are you feeling today?"
    assert body["action_cards"] == []
    assert WELCOME_TRIGGER not in body["reply"]
    assert len(calls) == 1
    assert calls[0]["user_message"] == WELCOME_USER_CUE
    assert calls[0]["persist_user_message"] is False
    assert calls[0]["opening_turn"] is True
    extraction.assert_not_awaited()
    stored = fake["messages"].docs
    assert stored
    assert all(doc.get("role") != "user" for doc in stored)
    assert all(decrypt_payload(doc.get("content", "")) != WELCOME_TRIGGER for doc in stored)
    assert fake["apm_nodes"].docs == []
    assert fake["apm_edges"].docs == []


def test_welcome_trigger_is_not_a_report_concern():
    moment = datetime(2026, 10, 1, 16, 15, tzinfo=timezone.utc)
    concerns = build_key_concerns(
        [
            {
                "role": "user",
                "content": WELCOME_TRIGGER,
                "created_at": moment,
            },
            {
                "role": "assistant",
                "content": "Hello Meera. How are you feeling today?",
                "created_at": moment,
            },
        ],
        "Asia/Kolkata",
    )
    assert concerns == {}
    assert WELCOME_TRIGGER not in str(concerns)


def test_welcome_trigger_rejects_a_mismatched_user():
    fake = _db()
    graph = AsyncMock()
    patches = _client(fake)
    with patches[0], patches[1], patch("services.graph.run_chat_graph", graph):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/chat/send",
            headers=_auth("user_a"),
            json={
                "user_id": "user_b",
                "session_id": "sess_123",
                "message": WELCOME_TRIGGER,
            },
        )
    app.dependency_overrides.clear()
    assert response.status_code == 403
    graph.assert_not_awaited()


def test_a_second_welcome_trigger_does_not_generate_again():
    fake = _db()
    calls = []
    patches = _client(fake)
    with patches[0], patches[1], patch(
        "services.graph.run_chat_graph", side_effect=_welcome_graph(calls)
    ):
        client = TestClient(app)
        payload = {
            "user_id": "user_a",
            "session_id": "sess_123",
            "message": WELCOME_TRIGGER,
        }
        first = client.post("/chat/send", headers=_auth(), json=payload)
        second = client.post("/chat/send", headers=_auth(), json=payload)
    app.dependency_overrides.clear()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["reply"] == second.json()["reply"]
    assert len(calls) == 1
    welcomes = [doc for doc in fake["messages"].docs if doc.get("message_kind") == "welcome"]
    assert len(welcomes) == 1


def test_similar_wording_is_a_normal_message():
    phrases = [
        "welcome message",
        "Welcome Message",
        "Can you give me a welcome message?",
        "I want a welcome",
    ]
    assert all(not is_welcome_trigger(phrase) for phrase in phrases)
    fake = _db()
    graph = AsyncMock(
        return_value={"session_id": "sess_123", "reply": "Tell me more.", "action_cards": []}
    )
    patches = _client(fake)
    with patches[0], patches[1], patch("api.routes.chat.run_chat_graph", graph), patch(
        "api.routes.chat.run_background_extraction", AsyncMock()
    ):
        client = TestClient(app)
        for phrase in phrases:
            response = client.post(
                "/chat/send",
                headers=_auth(),
                json={"user_id": "user_a", "session_id": "sess_123", "message": phrase},
            )
            assert response.status_code == 200
    app.dependency_overrides.clear()
    sent = [call.kwargs["user_message"] for call in graph.await_args_list]
    assert sent == phrases
    assert all(call.kwargs.get("opening_turn", False) is False for call in graph.await_args_list)


def _read_stream(client, payload, user_id="user_a"):
    response = client.post("/chat/stream", headers=_auth(user_id), json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return response.text


def test_stream_welcome_uses_sse_and_skips_the_stream_pipeline():
    fake = _db()
    calls = []
    stream = AsyncMock()
    patches = _client(fake)
    with patches[0], patches[1], patch(
        "services.graph.run_chat_graph", side_effect=_welcome_graph(calls)
    ), patch("api.routes.streaming.stream_chat_graph", stream):
        client = TestClient(app)
        body = _read_stream(
            client,
            {
                "user_id": "user_a",
                "session_id": "sess_123",
                "message": WELCOME_TRIGGER,
            },
        )
    app.dependency_overrides.clear()
    assert 'event: token\ndata: {"token": "Hello Meera. How are you feeling today?"}' in body
    assert 'event: done\ndata: {"status": "completed"}' in body
    assert WELCOME_TRIGGER not in body
    assert len(calls) == 1
    assert calls[0]["user_message"] == WELCOME_USER_CUE
    assert calls[0]["opening_turn"] is True
    stream.assert_not_awaited()
    assert all(doc.get("role") != "user" for doc in fake["messages"].docs)
    assert fake["apm_nodes"].docs == []


def test_stream_welcome_is_idempotent_with_send():
    fake = _db()
    calls = []
    patches = _client(fake)
    payload = {
        "user_id": "user_a",
        "session_id": "sess_123",
        "message": WELCOME_TRIGGER,
    }
    with patches[0], patches[1], patch(
        "services.graph.run_chat_graph", side_effect=_welcome_graph(calls)
    ):
        client = TestClient(app)
        first = client.post("/chat/send", headers=_auth(), json=payload)
        body = _read_stream(client, payload)
        again = _read_stream(client, payload)
    app.dependency_overrides.clear()
    assert first.status_code == 200
    reply = first.json()["reply"]
    assert reply in body and reply in again
    assert len(calls) == 1
    assert len([doc for doc in fake["messages"].docs if doc.get("message_kind") == "welcome"]) == 1


def test_normal_stream_does_not_call_the_welcome_service():
    fake = _db()
    seen = []

    async def _stream(**kwargs):
        seen.append(kwargs["user_message"])
        yield 'event: token\ndata: {"token": "I hear you."}\n\n'
        yield 'event: done\ndata: {"status": "completed"}\n\n'

    welcome = AsyncMock()
    patches = _client(fake)
    with patches[0], patches[1], patch(
        "api.routes.streaming.stream_chat_graph", _stream
    ), patch("api.routes.streaming.generate_welcome", welcome):
        client = TestClient(app)
        body = _read_stream(
            client,
            {
                "user_id": "user_a",
                "session_id": "sess_123",
                "message": "I feel stressed today.",
            },
        )
    app.dependency_overrides.clear()
    assert seen == ["I feel stressed today."]
    assert "event: token" in body
    assert "event: done" in body
    welcome.assert_not_awaited()


def test_stream_nearby_wording_stays_on_the_normal_pipeline():
    phrases = [
        "welcome message",
        "Welcome Message",
        "WELCOME MESSAGE!",
        "WELCOME MESSAGE PLEASE",
        "I want a welcome message",
    ]
    fake = _db()
    seen = []

    async def _stream(**kwargs):
        seen.append(kwargs["user_message"])
        yield 'event: token\ndata: {"token": "Tell me more."}\n\n'
        yield 'event: done\ndata: {"status": "completed"}\n\n'

    patches = _client(fake)
    with patches[0], patches[1], patch("api.routes.streaming.stream_chat_graph", _stream):
        client = TestClient(app)
        for phrase in phrases:
            body = _read_stream(
                client,
                {"user_id": "user_a", "session_id": "sess_123", "message": phrase},
            )
            assert "event: token" in body
    app.dependency_overrides.clear()
    assert seen == phrases


def test_stream_welcome_requires_the_token_owner():
    fake = _db()
    welcome = AsyncMock()
    patches = _client(fake)
    with patches[0], patches[1], patch("api.routes.streaming.generate_welcome", welcome):
        client = TestClient(app, raise_server_exceptions=False)
        anonymous = client.post(
            "/chat/stream",
            json={
                "user_id": "user_a",
                "session_id": "sess_123",
                "message": WELCOME_TRIGGER,
            },
        )
        foreign = client.post(
            "/chat/stream",
            headers=_auth("user_a"),
            json={
                "user_id": "user_b",
                "session_id": "sess_123",
                "message": WELCOME_TRIGGER,
            },
        )
    app.dependency_overrides.clear()
    assert anonymous.status_code == 401
    assert foreign.status_code == 403
    welcome.assert_not_awaited()

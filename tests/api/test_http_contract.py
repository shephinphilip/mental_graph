"""HTTP contract: mounting, auth, ownership, and error envelope."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app import app
from database import get_db
from services.users import issue_access_token
from tests.test_tracking import _db


def _client():
    fake = _db()
    app.dependency_overrides[get_db] = lambda: fake
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        return TestClient(app, raise_server_exceptions=False), fake


def _auth(user_id: str = "user_a"):
    return {"Authorization": "Bearer " + issue_access_token(user_id)}


def _mounted_paths():
    """Resolve included-router prefixes the way FastAPI does at request time."""
    found = set()
    from fastapi.routing import APIWebSocketRoute

    for route in app.router.routes:
        if isinstance(route, APIWebSocketRoute):
            found.add((route.path, "WS"))
            continue
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts:
            for ctx in contexts():
                for method in (ctx.methods or set()) - {"HEAD"}:
                    found.add((ctx.path, method))
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path:
            for method in methods - {"HEAD"}:
                found.add((path, method))
    return found


def test_compatibility_and_v1_paths_are_both_mounted():
    mounted = _mounted_paths()
    for path, method in [
        ("/auth/login", "POST"),
        ("/api/v1/auth/login", "POST"),
        ("/chat/send", "POST"),
        ("/api/v1/chat/send", "POST"),
        ("/api/mood", "POST"),
        ("/api/v1/mood", "POST"),
        ("/health", "GET"),
        ("/health/live", "GET"),
        ("/health/ready", "GET"),
        ("/voice/stt", "POST"),
        ("/api/v1/voice/stt", "POST"),
        ("/ws/psychiatrist-voice", "WS"),
        ("/api/v1/ws/psychiatrist-voice", "WS"),
    ]:
        assert (path, method) in mounted, f"{method} {path} is not mounted"


def test_unauthenticated_chat_is_401_with_request_id():
    client, _ = _client()
    response = client.post(
        "/chat/send",
        json={"user_id": "user_a", "session_id": "s1", "message": "hi"},
    )
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert response.headers.get("X-Request-ID")


def test_cross_user_chat_is_403():
    client, _ = _client()
    response = client.post(
        "/chat/send",
        headers=_auth("user_a"),
        json={"user_id": "user_b", "session_id": "s1", "message": "hi"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_chat_message_length_is_validated():
    from schemas import MAX_CHAT_MESSAGE_CHARS

    fake = _db()
    app.dependency_overrides[get_db] = lambda: fake
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    headers = _auth("user_a")
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ), patch("api.routes.chat.run_chat_graph", new_callable=AsyncMock) as chat:
        chat.return_value = {"session_id": "s1", "reply": "ok", "action_cards": []}
        client = TestClient(app, raise_server_exceptions=False)

        empty = client.post(
            "/chat/send",
            headers=headers,
            json={"user_id": "user_a", "session_id": "s1", "message": ""},
        )
        assert empty.status_code == 400
        assert empty.json()["error"]["code"] == "INVALID_REQUEST"
        assert empty.json()["detail"] == "Invalid request."

        normal = client.post(
            "/chat/send",
            headers=headers,
            json={"user_id": "user_a", "session_id": "s1", "message": "hi"},
        )
        assert normal.status_code == 200

        at_limit = client.post(
            "/chat/send",
            headers=headers,
            json={
                "user_id": "user_a",
                "session_id": "s1",
                "message": "h" * MAX_CHAT_MESSAGE_CHARS,
            },
        )
        assert at_limit.status_code == 200

        over = client.post(
            "/chat/send",
            headers=headers,
            json={
                "user_id": "user_a",
                "session_id": "s1",
                "message": "h" * (MAX_CHAT_MESSAGE_CHARS + 1),
            },
        )
        assert over.status_code == 400
        assert over.json()["success"] is False
        assert over.json()["error"]["code"] == "INVALID_REQUEST"
        assert over.headers.get("X-Request-ID")
        assert chat.call_count == 2


def test_v1_mood_uses_the_same_handler():
    client, _ = _client()
    response = client.post(
        "/api/v1/mood",
        headers=_auth(),
        json={"mood": "calm", "client_event_id": "contract-1"},
    )
    assert response.status_code == 200
    assert response.json()["entry"]["mood"] == "calm"


def test_health_live_does_not_require_mongo():
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/health/live").json()["status"] == "live"

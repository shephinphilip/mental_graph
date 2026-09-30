"""Standalone STT and psychiatrist-voice WebSocket — adapter, not a second brain."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from config.config import get_settings
from database import get_db
from integrations.sarvam import SarvamError
from backend_core.users import issue_access_token
from services.voice.audio import AudioValidationError, normalize_for_stt, silence_wav
from services.voice.session import create_session, reset_registry
from services.voice.tts import tts_language_code
from tests.test_tracking import _db


@pytest.fixture(autouse=True)
def _clean_voice_sessions():
    reset_registry()
    yield
    reset_registry()


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


def _wav():
    return silence_wav(400)


def test_normalize_wav_is_16k_mono():
    wav = normalize_for_stt(_wav())
    from services.voice.audio import read_wav

    info = read_wav(wav)
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.sample_width == 2


def test_empty_audio_rejected():
    with pytest.raises(AudioValidationError):
        normalize_for_stt(b"")


def test_invalid_audio_rejected():
    with pytest.raises(AudioValidationError):
        normalize_for_stt(b"not-a-wav-file")


def test_oversized_audio_rejected(monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_AUDIO_FILE_SIZE", 32)
    with pytest.raises(AudioValidationError):
        normalize_for_stt(_wav())


def test_stt_requires_auth():
    client, _ = _client()
    response = client.post("/api/v1/voice/stt", files={"audio": ("a.wav", _wav(), "audio/wav")})
    assert response.status_code == 401
    assert "secret" not in response.text.lower()


def test_stt_rejects_invalid_audio():
    client, _ = _client()
    response = client.post(
        "/api/v1/voice/stt",
        headers=_auth(),
        files={"audio": ("bad.bin", b"nope", "application/octet-stream")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False


def test_stt_rejects_oversized(monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_AUDIO_FILE_SIZE", 64)
    client, _ = _client()
    response = client.post(
        "/api/v1/voice/stt",
        headers=_auth(),
        files={"audio": ("big.wav", _wav(), "audio/wav")},
    )
    assert response.status_code == 400


@patch("services.voice.service.transcribe_wav", new_callable=AsyncMock)
def test_stt_returns_text_and_does_not_chat(transcribe):
    transcribe.return_value = {"text": "I've been feeling really overwhelmed today."}
    client, _ = _client()
    with patch("services.graph.run_chat_graph", new_callable=AsyncMock) as chat:
        response = client.post(
            "/api/v1/voice/stt",
            headers=_auth(),
            files={"audio": ("voice.wav", _wav(), "audio/wav")},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["text"] == "I've been feeling really overwhelmed today."
    chat.assert_not_called()


@patch("api.routes.voice.transcribe_audio", new_callable=AsyncMock)
def test_stt_never_echoes_api_key(transcribe, monkeypatch):
    monkeypatch.setattr(get_settings(), "SARVAM_API_KEY", "super-secret-sarvam-key")
    transcribe.return_value = {"success": True, "text": "hello"}
    client, _ = _client()
    response = client.post(
        "/api/v1/voice/stt",
        headers=_auth(),
        files={"audio": ("voice.wav", _wav(), "audio/wav")},
    )
    assert response.status_code == 200
    assert "super-secret-sarvam-key" not in response.text
    assert "SARVAM" not in response.text


@pytest.mark.asyncio
async def test_sarvam_stt_omits_language_hint():
    settings = get_settings()
    monkey_key = "test-key-not-real"
    with patch.object(settings, "SARVAM_API_KEY", monkey_key), patch(
        "integrations.sarvam.httpx.AsyncClient"
    ) as client_cls:
        instance = AsyncMock()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"transcript": "hello there"}
        instance.post = AsyncMock(return_value=response)
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = False
        client_cls.return_value = instance
        from integrations.sarvam import transcribe_wav

        result = await transcribe_wav(_wav())
        assert result["text"] == "hello there"
        kwargs = instance.post.call_args.kwargs
        assert kwargs["headers"]["api-subscription-key"] == monkey_key
        assert kwargs["data"]["model"] == settings.SARVAM_STT_MODEL
        assert "language_code" not in kwargs["data"]
        assert "language" not in kwargs["data"]


def test_tts_language_hinglish_is_roman_en_in():
    assert tts_language_code("HINGLISH") == "en-IN"
    assert tts_language_code("MALAYALAM") == "ml-IN"


def test_ws_rejects_unauthenticated():
    client, _ = _client()
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/psychiatrist-voice"):
            pass


def test_ws_rejects_query_user_id_for_auth():
    client, _ = _client()
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/psychiatrist-voice?user_id=user_a"):
            pass


@patch("api.routes.voice.run_voice_turn", new_callable=AsyncMock)
def test_ws_session_and_turn(run_turn):
    from services.voice.service import VoiceTurnResult

    run_turn.return_value = VoiceTurnResult(
        transcript="I feel overwhelmed",
        reply="That sounds like a lot to carry today.",
        action_cards=[{"card_type": "TASK_CARD", "title": "One small step", "action_payload": {}}],
        pcm=b"\x00\x00\x01\x00",
        sample_rate=24000,
        stt_latency_ms=12,
        llm_latency_ms=40,
        tts_latency_ms=20,
        tts_first_audio_ms=18,
        total_latency_ms=80,
    )
    client, _ = _client()
    with client.websocket_connect("/api/v1/ws/psychiatrist-voice", headers=_auth()) as ws:
        ws.send_json({"type": "session.start", "chat_session_id": "session_123"})
        started = ws.receive_json()
        assert started["type"] == "session.started"
        assert started["chat_session_id"] == "session_123"
        assert started["voice_session_id"]
        ws.send_json({"type": "audio.start"})
        ws.send_bytes(_wav())
        ws.send_json({"type": "audio.end"})
        ws.send_json({"type": "turn.end"})
        events = [ws.receive_json() for _ in range(4)]
        types = [item["type"] for item in events]
        assert "transcript.final" in types
        assert "assistant.text" in types
        assert "audio.chunk" in types
        assert "assistant.done" in types
        text_event = next(item for item in events if item["type"] == "assistant.text")
        assert text_event["action_cards"]
        chunk = next(item for item in events if item["type"] == "audio.chunk")
        assert chunk["encoding"] == "pcm_s16le"
        assert chunk["sample_rate"] == 24000
        assert base64.b64decode(chunk["data"])
        ws.send_json({"type": "session.end"})
        ended = ws.receive_json()
        assert ended["type"] == "session.ended"
    run_turn.assert_called_once()
    assert run_turn.call_args.kwargs["user_id"] == "user_a"
    assert run_turn.call_args.kwargs["session_id"] == "session_123"


@patch("api.routes.voice.run_voice_turn", new_callable=AsyncMock)
def test_ws_cancel_does_not_run_turn(run_turn):
    client, _ = _client()
    with client.websocket_connect("/ws/psychiatrist-voice", headers=_auth()) as ws:
        ws.send_json({"type": "session.start", "chat_session_id": "session_123"})
        assert ws.receive_json()["type"] == "session.started"
        ws.send_json({"type": "audio.start"})
        ws.send_bytes(_wav())
        ws.send_json({"type": "cancel"})
        ws.send_json({"type": "turn.end"})
        payload = ws.receive_json()
        assert payload["type"] == "error"
        run_turn.assert_not_called()


@patch("api.routes.voice.run_voice_turn", new_callable=AsyncMock)
def test_ws_user_cannot_bind_foreign_session(run_turn):
    create_session(
        user_id="user_a",
        chat_session_id="session_a",
        request_id="req_a",
        voice_session_id="voice_owned_by_a",
    )
    client, _ = _client()
    with client.websocket_connect("/ws/psychiatrist-voice", headers=_auth("user_b")) as ws:
        ws.send_json(
            {
                "type": "session.start",
                "chat_session_id": "session_a",
                "voice_session_id": "voice_owned_by_a",
            }
        )
        payload = ws.receive_json()
        assert payload["type"] == "error"
        assert payload["code"] == "FORBIDDEN"
    run_turn.assert_not_called()


@patch("api.routes.voice.run_voice_turn", new_callable=AsyncMock)
def test_ws_claimed_user_id_cannot_authorize(run_turn):
    client, _ = _client()
    with client.websocket_connect("/ws/psychiatrist-voice", headers=_auth("user_a")) as ws:
        ws.send_json(
            {
                "type": "session.start",
                "chat_session_id": "session_b",
                "user_id": "user_b",
            }
        )
        payload = ws.receive_json()
        assert payload["type"] == "error"
        assert payload["code"] == "FORBIDDEN"
    run_turn.assert_not_called()


@patch("services.voice.service.synthesize_pcm", new_callable=AsyncMock)
@patch("services.voice.service.run_chat_graph", new_callable=AsyncMock)
@patch("services.voice.service.transcribe_wav", new_callable=AsyncMock)
@patch(
    "services.voice.service.resolve_response_language",
    new_callable=AsyncMock,
)
@pytest.mark.asyncio
async def test_voice_turn_uses_chat_graph_and_stored_language(
    resolve_lang, transcribe, chat, tts
):
    transcribe.return_value = {"text": "I spoke English just now", "latency_ms": 9}
    chat.return_value = {
        "session_id": "session_123",
        "reply": "Njan undu. Parayu.",
        "action_cards": [],
    }
    resolve_lang.return_value = {
        "preferred_language": "MALAYALAM",
        "resolved_language": "MALAYALAM",
        "resolved_script": "MALAYALAM",
        "source": "DATABASE",
    }
    tts.return_value = {
        "pcm": b"\x00\x00",
        "sample_rate": 24000,
        "latency_ms": 5,
        "first_audio_ms": 4,
    }

    from services.voice.service import run_voice_turn

    result = await run_voice_turn(
        user_id="user_a",
        session_id="session_123",
        audio=_wav(),
        db=MagicMock(),
    )
    chat.assert_awaited_once()
    assert chat.await_args.kwargs["user_message"] == "I spoke English just now"
    assert chat.await_args.kwargs["user_id"] == "user_a"
    tts.assert_awaited_once()
    assert tts.await_args.kwargs["target_language_code"] == "ml-IN"
    assert result.reply == "Njan undu. Parayu."


@patch("services.voice.service.synthesize_pcm", new_callable=AsyncMock)
@patch("services.voice.service.run_chat_graph", new_callable=AsyncMock)
@patch("services.voice.service.transcribe_wav", new_callable=AsyncMock)
@patch(
    "services.voice.service.resolve_response_language",
    new_callable=AsyncMock,
)
@pytest.mark.asyncio
async def test_tts_failure_keeps_assistant_text(resolve_lang, transcribe, chat, tts):
    transcribe.return_value = {"text": "hello", "latency_ms": 1}
    chat.return_value = {"session_id": "s", "reply": "I'm here.", "action_cards": []}
    resolve_lang.return_value = {
        "preferred_language": "ENGLISH",
        "resolved_language": "ENGLISH",
        "resolved_script": "LATIN",
        "source": "DATABASE",
    }
    tts.side_effect = SarvamError("Text-to-speech timed out.")

    from services.voice.service import run_voice_turn

    result = await run_voice_turn(
        user_id="user_a",
        session_id="session_123",
        audio=_wav(),
        db=MagicMock(),
    )
    assert result.reply == "I'm here."
    assert result.tts_failed is True
    assert result.pcm == b""


@patch("services.voice.service.run_chat_graph", new_callable=AsyncMock)
@patch("services.voice.service.transcribe_wav", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_duplicate_voice_turn_skips_llm(transcribe, chat):
    transcribe.return_value = {"text": "same line", "latency_ms": 1}
    session = create_session(
        user_id="user_a",
        chat_session_id="session_123",
        request_id="r1",
    )
    session.mark_turn("same line")

    from services.voice.service import run_voice_turn

    result = await run_voice_turn(
        user_id="user_a",
        session_id="session_123",
        audio=_wav(),
        db=MagicMock(),
        live_session=session,
    )
    assert result.duplicate is True
    chat.assert_not_called()


def test_compatibility_stt_path_exists():
    client, _ = _client()
    response = client.post("/voice/stt", files={"audio": ("a.wav", _wav(), "audio/wav")})
    assert response.status_code == 401

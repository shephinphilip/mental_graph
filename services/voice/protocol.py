"""WebSocket event names for psychiatrist voice.

These names are new — the repo had no prior WebSocket protocol.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

CLIENT_SESSION_START = "session.start"
CLIENT_AUDIO_START = "audio.start"
CLIENT_AUDIO_END = "audio.end"
CLIENT_TURN_END = "turn.end"
CLIENT_SESSION_END = "session.end"
CLIENT_CANCEL = "cancel"

SERVER_SESSION_STARTED = "session.started"
SERVER_TRANSCRIPT_PARTIAL = "transcript.partial"
SERVER_TRANSCRIPT_FINAL = "transcript.final"
SERVER_ASSISTANT_TEXT = "assistant.text"
SERVER_AUDIO_CHUNK = "audio.chunk"
SERVER_ASSISTANT_DONE = "assistant.done"
SERVER_ERROR = "error"
SERVER_SESSION_ENDED = "session.ended"

CLIENT_EVENTS = frozenset(
    {
        CLIENT_SESSION_START,
        CLIENT_AUDIO_START,
        CLIENT_AUDIO_END,
        CLIENT_TURN_END,
        CLIENT_SESSION_END,
        CLIENT_CANCEL,
    }
)


def event(type_name: str, **fields: Any) -> Dict[str, Any]:
    payload = {"type": type_name}
    payload.update({key: value for key, value in fields.items() if value is not None})
    return payload


def error_event(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: str = "",
) -> Dict[str, Any]:
    return event(
        SERVER_ERROR,
        code=code,
        message=message,
        retryable=retryable,
        request_id=request_id or None,
    )


def parse_client_event(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind not in CLIENT_EVENTS:
        return None
    return payload

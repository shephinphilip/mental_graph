"""In-process voice session registry.

Voice turns persist through the existing ``messages`` collection via
``run_chat_graph``. This registry only tracks live WebSocket sessions.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Optional

from config.config import get_settings, logger

_lock = Lock()
_sessions: Dict[str, "VoiceSession"] = {}
_user_active: Dict[str, str] = {}


def user_id_hash(user_id: str) -> str:
    return hashlib.sha256((user_id or "").encode("utf-8")).hexdigest()[:12]


def new_voice_session_id() -> str:
    return f"voice_{uuid.uuid4().hex[:16]}"


def new_chat_session_id(user_id: str) -> str:
    return f"session_{user_id}_{uuid.uuid4().hex[:10]}"


@dataclass
class VoiceSession:
    voice_session_id: str
    user_id: str
    chat_session_id: str
    request_id: str
    created_at: datetime
    started_at: datetime
    ended_at: Optional[datetime] = None
    last_transcript_hash: str = ""
    last_turn_started: float = 0.0
    cancelled: bool = False
    audio_buffer: bytearray = field(default_factory=bytearray)
    capturing: bool = False

    def owns(self, user_id: str) -> bool:
        return self.user_id == user_id

    def reset_audio(self) -> None:
        self.audio_buffer = bytearray()
        self.capturing = False
        self.cancelled = False

    def mark_turn(self, transcript: str) -> bool:
        digest = hashlib.sha256((transcript or "").encode("utf-8")).hexdigest()
        if digest and digest == self.last_transcript_hash:
            return False
        self.last_transcript_hash = digest
        self.last_turn_started = time.monotonic()
        return True


def active_count() -> int:
    with _lock:
        return len(_sessions)


def get_session(voice_session_id: str) -> Optional[VoiceSession]:
    with _lock:
        return _sessions.get(voice_session_id)


def create_session(
    *,
    user_id: str,
    chat_session_id: str,
    request_id: str,
    voice_session_id: Optional[str] = None,
) -> VoiceSession:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with _lock:
        if voice_session_id:
            existing = _sessions.get(voice_session_id)
            if existing:
                if existing.user_id != user_id:
                    raise PermissionError("Voice session belongs to another user.")
                existing.chat_session_id = chat_session_id or existing.chat_session_id
                existing.request_id = request_id or existing.request_id
                return existing
        if len(_sessions) >= int(settings.MAX_CONCURRENT_VOICE_SESSIONS):
            raise OverflowError("Too many voice sessions.")
        prior = _user_active.get(user_id)
        if prior and prior in _sessions:
            raise OverflowError("A voice session is already open.")
        session = VoiceSession(
            voice_session_id=voice_session_id or new_voice_session_id(),
            user_id=user_id,
            chat_session_id=chat_session_id or new_chat_session_id(user_id),
            request_id=request_id,
            created_at=now,
            started_at=now,
        )
        _sessions[session.voice_session_id] = session
        _user_active[user_id] = session.voice_session_id
        logger.info(
            "voice_session_started user=%s voice_session=%s chat_session=%s request_id=%s",
            user_id_hash(user_id),
            session.voice_session_id,
            session.chat_session_id,
            request_id,
        )
        return session


def end_session(voice_session_id: str, user_id: str) -> Optional[VoiceSession]:
    with _lock:
        session = _sessions.get(voice_session_id)
        if session is None:
            return None
        if session.user_id != user_id:
            raise PermissionError("Voice session belongs to another user.")
        session.ended_at = datetime.now(timezone.utc)
        _sessions.pop(voice_session_id, None)
        if _user_active.get(user_id) == voice_session_id:
            _user_active.pop(user_id, None)
        logger.info(
            "voice_session_ended user=%s voice_session=%s request_id=%s",
            user_id_hash(user_id),
            voice_session_id,
            session.request_id,
        )
        return session


def reset_registry() -> None:
    """Test helper."""
    with _lock:
        _sessions.clear()
        _user_active.clear()

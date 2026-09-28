"""Standalone STT and the psychiatrist-voice WebSocket.

Both use the authenticated token. Client ``user_id`` is never an
authorization input. The spoken turn calls ``run_chat_graph`` — the same
brain as ``/chat/send``.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from config.config import get_settings, logger
from core.rate_limit import allow
from core.request_id import new_request_id
from database import get_db
from integrations.sarvam import SarvamError
from schemas import SpeechToTextResponse
from services.users import verify_access_token
from services.voice.audio import AudioValidationError, pcm_chunker
from services.voice.protocol import (
    CLIENT_AUDIO_END,
    CLIENT_AUDIO_START,
    CLIENT_CANCEL,
    CLIENT_SESSION_END,
    CLIENT_SESSION_START,
    CLIENT_TURN_END,
    SERVER_ASSISTANT_DONE,
    SERVER_ASSISTANT_TEXT,
    SERVER_AUDIO_CHUNK,
    SERVER_SESSION_ENDED,
    SERVER_SESSION_STARTED,
    SERVER_TRANSCRIPT_FINAL,
    error_event,
    event,
    parse_client_event,
)
from services.voice.service import run_voice_turn, schedule_extraction, transcribe_audio
from services.voice.session import (
    VoiceSession,
    create_session,
    end_session,
    user_id_hash,
)

router = APIRouter(tags=["voice"])


def _ws_token(websocket: WebSocket) -> str:
    header = websocket.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (websocket.query_params.get("token") or "").strip()


def _authenticate_socket(websocket: WebSocket) -> str:
    token = _ws_token(websocket)
    if not token:
        raise ValueError("Bearer token required")
    return verify_access_token(token)


async def _persist_voice_meta(db: AsyncIOMotorDatabase, session: VoiceSession) -> None:
    try:
        await db["voice_sessions"].update_one(
            {
                "user_id": session.user_id,
                "voice_session_id": session.voice_session_id,
            },
            {
                "$set": {
                    "chat_session_id": session.chat_session_id,
                    "request_id": session.request_id,
                    "started_at": session.started_at,
                    "ended_at": session.ended_at,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {"created_at": session.created_at},
            },
            upsert=True,
        )
    except Exception:
        logger.warning(
            "voice_session persist skipped user=%s",
            user_id_hash(session.user_id),
        )


@router.post("/voice/stt", response_model=SpeechToTextResponse)
async def speech_to_text(
    audio: UploadFile = File(...),
    authenticated_id: str = Depends(authenticated_user_id),
):
    """
    One-shot transcription for the chat input box.

    Does not call the LLM and does not persist a chat turn. The client
    must POST ``/chat/send`` after the user reviews the text.
    """
    del authenticated_id
    data = await audio.read()
    try:
        result = await transcribe_audio(
            data,
            filename=audio.filename or "",
            content_type=audio.content_type or "",
        )
    except AudioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SarvamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return SpeechToTextResponse(success=True, text=result["text"])


async def psychiatrist_voice(
    websocket: WebSocket,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        user_id = _authenticate_socket(websocket)
    except ValueError:
        await websocket.close(code=4401)
        return

    settings = get_settings()
    identity = _ws_token(websocket)[-32:]
    if settings.RATE_LIMIT_ENABLED and not allow(
        f"voice-session:{identity}",
        settings.RATE_LIMIT_VOICE_SESSION_PER_MINUTE,
        60,
    ):
        await websocket.close(code=4429)
        return

    await websocket.accept()
    session: Optional[VoiceSession] = None
    request_id = new_request_id()

    async def send(payload: dict) -> None:
        await websocket.send_json(payload)

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                if session is None or not session.capturing or session.cancelled:
                    continue
                chunk = message["bytes"]
                limit = int(settings.MAX_AUDIO_FILE_SIZE)
                if len(session.audio_buffer) + len(chunk) > limit:
                    await send(
                        error_event(
                            "AUDIO_TOO_LARGE",
                            "Audio is too large.",
                            request_id=request_id,
                        )
                    )
                    session.reset_audio()
                    continue
                session.audio_buffer.extend(chunk)
                continue

            text = message.get("text")
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                await send(
                    error_event("INVALID_REQUEST", "Invalid message.", request_id=request_id)
                )
                continue
            parsed = parse_client_event(payload)
            if parsed is None:
                await send(
                    error_event("INVALID_REQUEST", "Unknown event type.", request_id=request_id)
                )
                continue

            kind = parsed["type"]
            if kind == CLIENT_SESSION_START:
                claimed_user = parsed.get("user_id")
                if claimed_user and claimed_user != user_id:
                    await send(
                        error_event(
                            "FORBIDDEN",
                            "You cannot access that resource.",
                            request_id=request_id,
                        )
                    )
                    await websocket.close(code=4403)
                    return
                try:
                    session = create_session(
                        user_id=user_id,
                        chat_session_id=str(parsed.get("chat_session_id") or ""),
                        request_id=request_id,
                        voice_session_id=parsed.get("voice_session_id"),
                    )
                except PermissionError:
                    await send(
                        error_event(
                            "FORBIDDEN",
                            "You cannot access that resource.",
                            request_id=request_id,
                        )
                    )
                    await websocket.close(code=4403)
                    return
                except OverflowError as exc:
                    await send(
                        error_event("RATE_LIMITED", str(exc), request_id=request_id)
                    )
                    await websocket.close(code=4429)
                    return
                await _persist_voice_meta(db, session)
                await send(
                    event(
                        SERVER_SESSION_STARTED,
                        voice_session_id=session.voice_session_id,
                        chat_session_id=session.chat_session_id,
                        request_id=session.request_id,
                    )
                )
                continue

            if session is None:
                await send(
                    error_event(
                        "INVALID_REQUEST",
                        "Start a voice session first.",
                        request_id=request_id,
                    )
                )
                continue

            if kind == CLIENT_AUDIO_START:
                session.reset_audio()
                session.capturing = True
                continue

            if kind == CLIENT_AUDIO_END:
                session.capturing = False
                continue

            if kind == CLIENT_CANCEL:
                session.reset_audio()
                session.cancelled = True
                continue

            if kind == CLIENT_SESSION_END:
                ended = end_session(session.voice_session_id, user_id)
                if ended:
                    await _persist_voice_meta(db, ended)
                await send(
                    event(
                        SERVER_SESSION_ENDED,
                        voice_session_id=session.voice_session_id,
                        chat_session_id=session.chat_session_id,
                    )
                )
                await websocket.close(code=1000)
                return

            if kind == CLIENT_TURN_END:
                session.capturing = False
                audio = bytes(session.audio_buffer)
                session.reset_audio()
                if not audio:
                    await send(
                        error_event(
                            "INVALID_REQUEST",
                            "No audio received.",
                            request_id=request_id,
                        )
                    )
                    continue
                try:
                    turn = await run_voice_turn(
                        user_id=user_id,
                        session_id=session.chat_session_id,
                        audio=audio,
                        db=db,
                        voice_session_id=session.voice_session_id,
                        request_id=request_id,
                        live_session=session,
                    )
                except AudioValidationError as exc:
                    await send(
                        error_event("INVALID_REQUEST", str(exc), request_id=request_id)
                    )
                    continue
                except ValueError as exc:
                    await send(
                        error_event("INVALID_REQUEST", str(exc), request_id=request_id)
                    )
                    continue
                except SarvamError as exc:
                    await send(
                        error_event(
                            "STT_FAILED",
                            str(exc),
                            retryable=True,
                            request_id=request_id,
                        )
                    )
                    continue
                except asyncio.TimeoutError:
                    await send(
                        error_event(
                            "LLM_TIMEOUT",
                            "The reply took too long.",
                            retryable=True,
                            request_id=request_id,
                        )
                    )
                    continue
                except Exception:
                    logger.exception(
                        "voice_turn_failed user=%s request_id=%s",
                        user_id_hash(user_id),
                        request_id,
                    )
                    await send(
                        error_event(
                            "INTERNAL_ERROR",
                            "An unexpected error occurred.",
                            request_id=request_id,
                        )
                    )
                    continue

                if turn.duplicate:
                    await send(
                        event(
                            SERVER_ASSISTANT_DONE,
                            duplicate=True,
                            chat_session_id=session.chat_session_id,
                        )
                    )
                    continue

                await send(event(SERVER_TRANSCRIPT_FINAL, text=turn.transcript))
                await send(
                    event(
                        SERVER_ASSISTANT_TEXT,
                        text=turn.reply,
                        action_cards=turn.action_cards,
                    )
                )
                if turn.tts_failed:
                    await send(
                        error_event(
                            "TTS_FAILED",
                            "Spoken audio could not be generated. The text reply is available.",
                            retryable=True,
                            request_id=request_id,
                        )
                    )
                else:
                    for chunk in pcm_chunker(turn.pcm):
                        await send(
                            event(
                                SERVER_AUDIO_CHUNK,
                                encoding="pcm_s16le",
                                sample_rate=turn.sample_rate,
                                channels=1,
                                data=base64.b64encode(chunk).decode("ascii"),
                            )
                        )
                await send(
                    event(
                        SERVER_ASSISTANT_DONE,
                        chat_session_id=session.chat_session_id,
                        stt_latency_ms=turn.stt_latency_ms,
                        llm_latency_ms=turn.llm_latency_ms,
                        tts_latency_ms=turn.tts_latency_ms,
                        first_audio_latency_ms=turn.tts_first_audio_ms,
                        total_turn_latency_ms=turn.total_latency_ms,
                    )
                )
                asyncio.create_task(
                    schedule_extraction(
                        user_id=user_id,
                        session_id=session.chat_session_id,
                        transcript=turn.transcript,
                        reply=turn.reply,
                        db=db,
                    )
                )
    except WebSocketDisconnect:
        pass
    finally:
        if session is not None:
            try:
                ended = end_session(session.voice_session_id, user_id)
                if ended:
                    await _persist_voice_meta(db, ended)
            except PermissionError:
                pass

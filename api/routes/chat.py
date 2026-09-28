"""Chat routes: AI-initiated welcome, standard send, and session resume.

The SSE streaming endpoint lives in ``api/routes/streaming.py``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import assert_owner, authenticated_user_id
from config.config import logger
from database import get_db
from prompts import WELCOME_USER_CUE
from schemas import (
    ChatMessageRequest,
    ChatMessageResponse,
    SessionResumeResponse,
    WelcomeRequest,
)
from services.extraction import run_background_extraction
from services.graph import run_chat_graph
from services.security import CryptoIntegrityError
from services.session_resume import resume_user_session

router = APIRouter(tags=["chat"])


@router.post("/chat/welcome", response_model=ChatMessageResponse)
async def welcome_message(
    payload: WelcomeRequest,
    authenticated_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    AI-initiated opening for a new session. Persists only the assistant turn.

    Idempotent: if a welcome (``message_kind=welcome``) already exists for the
    session, return it. If the session already has other messages, do not
    insert a new welcome — return the latest assistant line when available.
    """
    from services.chat_history import (
        get_latest_message,
        get_welcome_message,
        session_has_any_messages,
        session_has_user_messages,
        update_welcome_message,
    )
    from services.language_preferences import resolve_response_language
    from services.security import decrypt_payload

    assert_owner(payload.user_id, authenticated_id)

    resolved = await resolve_response_language(
        db, authenticated_id, opening_turn=True
    )
    existing_welcome = await get_welcome_message(
        db, payload.user_id, payload.session_id
    )
    welcome_current = bool(
        existing_welcome
        and existing_welcome.get("response_language") == resolved["resolved_language"]
        and existing_welcome.get("response_script") == resolved["resolved_script"]
    )
    has_user_turn = await session_has_user_messages(
        db, payload.user_id, payload.session_id
    )
    if existing_welcome and (welcome_current or has_user_turn):
        return ChatMessageResponse(
            session_id=payload.session_id,
            reply=decrypt_payload(existing_welcome.get("content", "")),
            action_cards=[],
        )

    if has_user_turn or (
        not existing_welcome
        and await session_has_any_messages(db, payload.user_id, payload.session_id)
    ):
        latest = await get_latest_message(db, payload.user_id, payload.session_id)
        reply = ""
        if latest and latest.get("role") == "assistant":
            reply = decrypt_payload(latest.get("content", ""))
        return ChatMessageResponse(
            session_id=payload.session_id,
            reply=reply,
            action_cards=[],
        )

    try:
        response_data = await run_chat_graph(
            user_id=payload.user_id,
            session_id=payload.session_id,
            user_message=WELCOME_USER_CUE,
            db=db,
            persist_user_message=False,
            opening_turn=True,
        )
        if existing_welcome and not welcome_current:
            reply = (
                response_data["reply"]
                if isinstance(response_data, dict)
                else response_data.reply
            )
            await update_welcome_message(
                db,
                user_id=payload.user_id,
                session_id=payload.session_id,
                content=reply,
                response_language=resolved["resolved_language"],
                response_script=resolved["resolved_script"],
            )
        return response_data
    except CryptoIntegrityError:
        raise
    except Exception as exc:
        logger.exception("Welcome turn failed for user=%s: %s", payload.user_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/chat/send", response_model=ChatMessageResponse)
async def send_message(
    payload: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    authenticated_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Process an incoming chat message and return an AI response (JSON).

    Executes the full LangGraph pipeline synchronously and returns the complete reply.
    """
    assert_owner(payload.user_id, authenticated_id)
    try:
        logger.info(
            "Incoming JSON message — user=%s session=%s",
            payload.user_id,
            payload.session_id,
        )

        response_data = await run_chat_graph(
            user_id=payload.user_id,
            session_id=payload.session_id,
            user_message=payload.message,
            db=db,
        )

        background_tasks.add_task(
            run_background_extraction,
            user_id=payload.user_id,
            session_id=payload.session_id,
            message=payload.message,
            reply=response_data["reply"],
            db=db,
        )

        return response_data

    except CryptoIntegrityError:
        raise
    except Exception as exc:
        logger.exception(
            "Unhandled error in /chat/send for user=%s session=%s: %s",
            payload.user_id,
            payload.session_id,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/chat/session/{user_id}/resume", response_model=SessionResumeResponse)
async def resume_session(
    user_id: str,
    session_id: Optional[str] = None,
    authenticated_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Rapid session resumption endpoint (< 500 ms SLA).
    """
    assert_owner(user_id, authenticated_id)
    try:
        return await resume_user_session(db, user_id, session_id)
    except CryptoIntegrityError:
        raise
    except Exception as exc:
        logger.exception(
            "Session resumption failed for user=%s session=%s: %s",
            user_id,
            session_id,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc))

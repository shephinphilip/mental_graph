"""Session opening shared by chat send, chat stream, and the deprecated welcome route.

The literal command ``WELCOME MESSAGE`` is an internal control signal.
It is never stored as a user turn and never passed to the chat graph.
The model sees the existing welcome cue instead.
"""

from __future__ import annotations

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import logger
from prompts import WELCOME_USER_CUE
from backend_core.security import CryptoIntegrityError, decrypt_payload

WELCOME_TRIGGER = "WELCOME MESSAGE"


def is_welcome_trigger(message: str) -> bool:
    """Exact command only. Nearby student wording stays a normal turn."""
    return message == WELCOME_TRIGGER


def accept_welcome_command(
    message: str,
    *,
    channel: str,
    user_id: str,
    session_id: str,
) -> bool:
    """True only for the exact internal command. Logs the route decision."""
    if not is_welcome_trigger(message):
        return False
    logger.info(
        "Detected internal chat command: WELCOME MESSAGE channel=%s user=%s session=%s routed=welcome_service",
        channel,
        user_id,
        session_id,
    )
    return True


async def generate_welcome(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
) -> dict:
    """Return the session opening. One welcome document per session and language.

    A second call for the same user, session, and language returns the stored
    assistant line. The trigger text is not an argument and is not persisted.
    """
    from services.chat_history import (
        get_latest_message,
        get_welcome_message,
        session_has_any_messages,
        session_has_user_messages,
        update_welcome_message,
    )
    from services.graph import run_chat_graph
    from services.language_preferences import resolve_response_language

    resolved = await resolve_response_language(db, user_id, opening_turn=True)
    existing_welcome = await get_welcome_message(db, user_id, session_id)
    welcome_current = bool(
        existing_welcome
        and existing_welcome.get("response_language") == resolved["resolved_language"]
        and existing_welcome.get("response_script") == resolved["resolved_script"]
    )
    has_user_turn = await session_has_user_messages(db, user_id, session_id)
    if existing_welcome and (welcome_current or has_user_turn):
        return {
            "session_id": session_id,
            "reply": decrypt_payload(existing_welcome.get("content", "")),
            "action_cards": [],
        }

    if has_user_turn or (
        not existing_welcome
        and await session_has_any_messages(db, user_id, session_id)
    ):
        latest = await get_latest_message(db, user_id, session_id)
        reply = ""
        if latest and latest.get("role") == "assistant":
            reply = decrypt_payload(latest.get("content", ""))
        return {
            "session_id": session_id,
            "reply": reply,
            "action_cards": [],
        }

    try:
        response_data = await run_chat_graph(
            user_id=user_id,
            session_id=session_id,
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
                user_id=user_id,
                session_id=session_id,
                content=reply,
                response_language=resolved["resolved_language"],
                response_script=resolved["resolved_script"],
            )
        return response_data
    except CryptoIntegrityError:
        raise
    except Exception as exc:
        logger.exception("Welcome turn failed for user=%s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

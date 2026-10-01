"""SSE streaming chat route.

Kept in its own module because its response type (``StreamingResponse``) and
resilience contract differ from the JSON chat routes, but it shares the same
authentication, context, language, safety, memory, pattern, and meditation
decisions via ``services/streaming.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import assert_owner, authenticated_user_id
from config.config import logger
from database import get_db
from schemas import ChatMessageRequest
from backend_core.security import CryptoIntegrityError
from services.streaming import stream_chat_graph, stream_welcome_reply
from services.welcome import accept_welcome_command, generate_welcome

router = APIRouter(tags=["chat"])


@router.post("/chat/stream")
async def stream_message(
    payload: ChatMessageRequest,
    authenticated_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Stream the AI response in real-time using Server-Sent Events (SSE).

    The exact body text ``WELCOME MESSAGE`` is an internal frontend command.
    It uses the shared welcome service and is returned as ``token`` then
    ``done`` events. It is not stored as a student message. Any other text
    keeps the normal streaming pipeline.
    """
    assert_owner(payload.user_id, authenticated_id)
    try:
        if accept_welcome_command(
            payload.message,
            channel="stream",
            user_id=payload.user_id,
            session_id=payload.session_id,
        ):
            welcome = await generate_welcome(
                db, user_id=payload.user_id, session_id=payload.session_id
            )
            generator = stream_welcome_reply(welcome)
        else:
            logger.info(
                "Incoming SSE stream request — user=%s session=%s",
                payload.user_id,
                payload.session_id,
            )
            generator = stream_chat_graph(
                user_id=payload.user_id,
                session_id=payload.session_id,
                user_message=payload.message,
                db=db,
            )

        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except CryptoIntegrityError:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to initiate SSE stream for user=%s session=%s: %s",
            payload.user_id,
            payload.session_id,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc))

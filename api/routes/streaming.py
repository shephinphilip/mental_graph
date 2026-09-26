"""SSE streaming chat route.

Kept in its own module because its response type (``StreamingResponse``) and
resilience contract differ from the JSON chat routes, but it shares the same
authentication, context, language, safety, memory, pattern, and meditation
decisions via ``services/streaming.py``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import assert_owner, authenticated_user_id
from database import get_db
from schemas import ChatMessageRequest
from services.extraction import run_background_extraction
from services.streaming import stream_chat_graph

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat/stream")
async def stream_message(
    payload: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    authenticated_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Stream the AI response in real-time using Server-Sent Events (SSE).
    """
    assert_owner(payload.user_id, authenticated_id)
    try:
        logger.info(
            "Incoming SSE stream request — user=%s session=%s",
            payload.user_id,
            payload.session_id,
        )

        background_tasks.add_task(
            run_background_extraction,
            user_id=payload.user_id,
            session_id=payload.session_id,
            message=payload.message,
            reply="[Streamed Response]",
            db=db,
        )

        return StreamingResponse(
            stream_chat_graph(
                user_id=payload.user_id,
                session_id=payload.session_id,
                user_message=payload.message,
                db=db,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except Exception as exc:
        logger.exception(
            "Failed to initiate SSE stream for user=%s session=%s: %s",
            payload.user_id,
            payload.session_id,
            exc,
        )
        raise HTTPException(status_code=500, detail=str(exc))

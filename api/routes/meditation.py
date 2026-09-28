"""Meditation HTTP routes. Ranking stays in ``services/meditation/``."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from api.presenters import public_execution
from config.config import logger
from database import get_db
from schemas import (
    MeditationCompleteRequest,
    MeditationFeedbackRequest,
    MeditationPreviewRequest,
    MeditationStartRequest,
)

router = APIRouter(tags=["meditation"])


@router.post("/meditation/preview")
async def meditation_preview(
    payload: MeditationPreviewRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Developer preview of the ranked practice. Debug is not sent on chat cards."""
    from services.meditation.service import preview_for_user

    return await preview_for_user(db, user_id, payload.message)


@router.post("/meditation/start")
async def meditation_start(
    payload: MeditationStartRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from services.meditation.service import start_execution

    try:
        doc = await start_execution(
            db,
            user_id=user_id,
            meditation_id=payload.meditation_id,
            execution_nonce=payload.execution_nonce,
            session_id=payload.session_id or "",
            reason=payload.reason or "",
        )
    except ValueError as exc:
        logger.warning("Meditation start rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return public_execution(doc)


@router.post("/meditation/complete")
async def meditation_complete(
    payload: MeditationCompleteRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from services.meditation.service import complete_execution

    try:
        doc = await complete_execution(
            db,
            user_id=user_id,
            execution_id=payload.execution_id,
            execution_nonce=payload.execution_nonce,
            listen_duration_seconds=payload.listen_duration_seconds,
        )
    except ValueError as exc:
        logger.warning("Meditation complete rejected: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user_id)
    return public_execution(doc)


@router.post("/meditation/feedback")
async def meditation_feedback(
    payload: MeditationFeedbackRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from services.meditation.service import record_feedback

    try:
        doc = await record_feedback(
            db,
            user_id=user_id,
            execution_id=payload.execution_id,
            execution_nonce=payload.execution_nonce,
            feedback=payload.feedback,
        )
    except ValueError as exc:
        message = str(exc)
        status = 400 if "Unsupported" in message else 404
        logger.warning("Meditation feedback rejected: %s", exc)
        raise HTTPException(status_code=status, detail=message) from exc
    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user_id)
    return public_execution(doc)

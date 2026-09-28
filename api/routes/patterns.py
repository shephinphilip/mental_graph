"""Pattern-feedback HTTP route. Detection stays in ``services/patterns/``."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from config.config import logger
from database import get_db
from schemas import PatternFeedbackRequest

router = APIRouter(tags=["patterns"])


@router.post("/patterns/feedback")
async def pattern_feedback(
    payload: PatternFeedbackRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Explicit user confirmation/disagreement on a detected pattern."""
    from services.patterns import record_pattern_feedback

    try:
        recorded = await record_pattern_feedback(
            db,
            user_id=user_id,
            pattern_id=payload.pattern_id,
            event_type=payload.event_type,
            note=payload.note,
        )
    except ValueError as exc:
        logger.warning("Pattern feedback rejected: %s", exc)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user_id)
    return recorded

"""Exam Buddy HTTP entry. The token owner is the only graph key."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from api.deps import assert_owner, authenticated_user_id
from database import get_db
from exam_buddy_guardrails.services.exam_buddy_service import handle_exam_buddy_turn

router = APIRouter(tags=["exam-buddy"])


class ExamBuddyRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    user_id: Optional[str] = None


@router.post("/exam-buddy/ask")
async def ask_exam_buddy(
    payload: ExamBuddyRequest,
    background_tasks: BackgroundTasks,
    authenticated_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Academic tutor turn. A body user id cannot select another student's memory."""
    if payload.user_id:
        assert_owner(payload.user_id, authenticated_id)
    result = await handle_exam_buddy_turn(
        db,
        authenticated_id,
        payload.message,
        background_tasks,
    )
    return result.model_dump()

"""Mood and habit HTTP routes. Persistence stays in ``tracking/``."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id, task_http
from config.config import get_settings, logger
from database import get_db
from schemas import (
    HabitCheckInRequest,
    HabitCreateRequest,
    HabitPatchRequest,
    MoodLogRequest,
    StreakVisibilityRequest,
)

router = APIRouter(tags=["tracking"])


@router.post("/mood")
async def log_mood_check_in(
    payload: MoodLogRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """One check-in for the authenticated user. A body user_id cannot retarget it."""
    from tracking.mood import log_mood

    try:
        logged = await log_mood(
            db,
            user_id,
            mood=payload.mood,
            score=payload.score,
            note=payload.note,
            input_format=payload.input_format,
            client_event_id=payload.client_event_id,
            logged_at=payload.logged_at,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        logger.warning("Mood log rejected: %s", exc)
        raise task_http(exc) from exc
    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user_id)
    return logged


@router.get("/mood/recent")
async def recent_mood_check_ins(
    days: Optional[int] = None,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from tracking.mood import recent_moods

    window = days
    if window is not None:
        window = max(1, min(int(window), get_settings().MAX_LIMIT))
    return {"moods": await recent_moods(db, user_id, days=window)}


@router.get("/habits")
async def list_user_habits(
    include_archived: bool = False,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from tracking.habits import list_habits

    try:
        return await list_habits(db, user_id, include_archived=include_archived)
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc


@router.post("/habits")
async def create_user_habit(
    payload: HabitCreateRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Habits are set by the person, not assigned."""
    from tracking.habits import create_habit

    try:
        return await create_habit(
            db,
            user_id,
            title=payload.title,
            frequency=payload.frequency,
            reminder_time=payload.reminder_time,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc


@router.post("/habits/streaks")
async def set_habit_streak_visibility(
    payload: StreakVisibilityRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Opting out hides streaks from the chatbot as well as the screen."""
    from tracking.habits import set_streak_visibility

    return await set_streak_visibility(db, user_id, payload.show_streaks)


@router.patch("/habits/{habit_id}")
async def patch_user_habit(
    habit_id: str,
    payload: HabitPatchRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Rename, retime, pause, or archive. Completion history is never rewritten."""
    from tracking.habits import update_habit

    try:
        return await update_habit(
            db,
            user_id,
            habit_id,
            title=payload.title,
            frequency=payload.frequency,
            reminder_time=payload.reminder_time,
            status=payload.status,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc


@router.post("/habits/{habit_id}/check-in")
async def check_in_user_habit(
    habit_id: str,
    payload: HabitCheckInRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Idempotent: the same day twice returns `already_logged: true`."""
    from tracking.habits import check_in

    try:
        logged = await check_in(
            db,
            user_id,
            habit_id,
            on_date=payload.on_date,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc
    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user_id)
    return logged

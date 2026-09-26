"""Sleep HTTP routes. ``sleep_logs`` stays owned by ``sleep/``."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id, task_http
from api.presenters import public_sleep
from config import get_settings
from database import get_db
from schemas import SleepLogRequest

router = APIRouter(tags=["sleep"])


def _clamp_days(days: int) -> int:
    settings = get_settings()
    return max(1, min(int(days), settings.MAX_LIMIT))


@router.post("/sleep")
async def create_sleep(
    payload: SleepLogRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Store one night for the authenticated user. A body user_id cannot retarget it."""
    from sleep.writer import create_sleep_log

    try:
        doc = await create_sleep_log(
            db,
            user_id,
            bedtime=payload.bedtime,
            wake_up_time=payload.wake_up_time,
            date=payload.date,
            total_duration_minutes=payload.total_duration_minutes,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc
    return public_sleep(doc)


@router.get("/sleep/recent")
async def recent_sleep(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from sleep.reader import get_recent_sleep

    doc = await get_recent_sleep(db, user_id)
    return {"sleep": public_sleep(doc) if doc else None}


@router.get("/sleep/history")
async def sleep_history(
    days: int = 7,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from sleep.reader import get_sleep_history

    docs = await get_sleep_history(db, user_id, days=_clamp_days(days))
    return {"sleep": [public_sleep(doc) for doc in docs]}

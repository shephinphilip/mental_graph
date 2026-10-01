"""Timezone preference. Canonical source: ``users.timezone`` (IANA)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from config.config import logger
from database import get_db
from schemas import TimezonePreferenceRequest
from services.timezone_preferences import set_timezone

router = APIRouter(tags=["timezone"])


@router.post("/timezone")
async def update_timezone(
    payload: TimezonePreferenceRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Store the signed-in user's IANA timezone. A body user id is not a target."""
    try:
        updated = await set_timezone(db, user_id, payload.timezone)
    except ValueError as exc:
        logger.warning("Timezone update rejected: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Active user not found")
    return {
        "success": True,
        "user_id": user_id,
        "timezone": updated.get("timezone"),
    }

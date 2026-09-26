"""Journal HTTP routes. Persistence stays in ``journaling/service.py``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id, task_http
from api.presenters import public_journal
from database import get_db
from schemas import JournalEntryRequest

router = APIRouter(tags=["journal"])


@router.post("/journal/entry")
async def post_journal_entry(
    payload: JournalEntryRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import create_journal_entry

    try:
        doc = await create_journal_entry(
            db,
            user_id,
            title=payload.title,
            content=payload.content,
            mood=payload.mood,
            tags=payload.tags,
            time_spent=payload.time_spent,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc
    return public_journal(doc, preview=False)


@router.get("/journal/recent-entries")
async def journal_recent(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import recent_entries

    rows = await recent_entries(db, user_id)
    return {"entries": [public_journal(row, preview=True) for row in rows]}


@router.get("/journal/entry/{entry_id}")
async def journal_entry(
    entry_id: str,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import get_entry

    doc = await get_entry(db, user_id, entry_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return public_journal(doc, preview=False)


@router.get("/journal/past-reflections")
async def journal_past(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import past_reflections

    rows = await past_reflections(db, user_id)
    return {"entries": [public_journal(row, preview=True) for row in rows]}


@router.get("/journal/calendar-data")
async def journal_calendar(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import calendar_data

    return {"days": await calendar_data(db, user_id)}


@router.get("/journal/favorites")
async def journal_favorites(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import favorites

    rows = await favorites(db, user_id)
    return {"entries": [public_journal(row, preview=True) for row in rows]}


@router.get("/journal/stats")
async def journal_stats(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import stats

    return await stats(db, user_id)


@router.get("/journal/monthly-mindfulness")
async def journal_month(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from journaling.service import monthly_mindfulness

    return await monthly_mindfulness(db, user_id)

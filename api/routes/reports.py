"""Session-report HTTP routes. Readings stay in ``reports/`` + ``services/session_report.py``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id, task_http
from database import get_db
from schemas import AcceptReportTaskRequest, SessionReportRequest

router = APIRouter(tags=["reports"])


@router.post("/reports/tasks/accept")
async def accept_report_task(
    payload: AcceptReportTaskRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Add one proposed report task to today's list. The body user id is not the target."""
    from reports.store import accept_proposed_task

    try:
        if payload.user_id and payload.user_id != user_id:
            raise PermissionError("Cannot add another user's task")
        return await accept_proposed_task(
            db,
            user_id,
            payload.session_id,
            payload.task_id,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc


@router.post("/session/report")
async def session_report(
    payload: SessionReportRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Post-conversation reading. Ranks at most one meditation from the report state."""
    from services.session_report import generate_session_report

    try:
        return await generate_session_report(
            db, user_id=user_id, session_id=payload.session_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

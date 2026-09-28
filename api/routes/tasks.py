"""Daily-task HTTP routes. ``daily_tasks`` stays owned by ``tasks/store.py``."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id, task_http
from config.config import logger
from database import get_db
from schemas import TaskCompleteRequest, TaskCustomPatch, TaskCustomRequest

router = APIRouter(tags=["tasks"])


@router.get("/report_card/tasks/{claimed_user_id}")
async def report_card_tasks(
    claimed_user_id: str,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from tasks.store import list_today

    try:
        return await list_today(db, user_id, claimed_user_id=claimed_user_id)
    except PermissionError as exc:
        logger.warning("Task list rejected: %s", exc)
        raise task_http(exc) from exc


@router.post("/report_card/tasks/complete")
async def report_card_complete(
    payload: TaskCompleteRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from tasks.store import complete_task

    try:
        completed = await complete_task(
            db, user_id, payload.task_id, claimed_user_id=payload.user_id
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc
    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user_id)
    return completed


@router.post("/report_card/tasks/custom")
async def report_card_custom(
    payload: TaskCustomRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from tasks.store import add_custom_task

    try:
        return await add_custom_task(
            db,
            user_id,
            title=payload.title,
            description=payload.description,
            claimed_user_id=payload.user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc


@router.patch("/report_card/tasks/custom/{claimed_user_id}/{task_id}")
async def report_card_patch_custom(
    claimed_user_id: str,
    task_id: str,
    payload: TaskCustomPatch,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from tasks.store import patch_custom_task

    try:
        return await patch_custom_task(
            db,
            user_id,
            task_id,
            title=payload.title,
            description=payload.description,
            is_deleted=payload.is_deleted,
            claimed_user_id=claimed_user_id,
        )
    except (PermissionError, LookupError, ValueError) as exc:
        raise task_http(exc) from exc

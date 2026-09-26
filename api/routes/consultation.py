"""Consultation evaluation HTTP routes. Decisions stay in ``consultation/``."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from database import get_db
from schemas import (
    ConsultationBatchRequest,
    ConsultationManualRequest,
    ConsultationOverrideRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["consultation"])


@router.post("/consultation-evaluation/manual")
async def consultation_manual(
    payload: ConsultationManualRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from consultation.evaluate import evaluate_user_for_consultation
    from consultation.roles import target_user

    try:
        target = await target_user(db, user_id, payload.user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return await evaluate_user_for_consultation(db, target, trigger="MANUAL", actor=user_id)


@router.post("/consultation-evaluation/manual-override")
async def consultation_manual_override(
    payload: ConsultationOverrideRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from consultation.evaluate import manual_override
    from consultation.roles import is_staff

    if not await is_staff(db, user_id):
        raise HTTPException(status_code=403, detail="Staff role required")
    try:
        return await manual_override(
            db, payload.user_id, status=payload.status, reason=payload.reason, actor=user_id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/consultation-evaluation/batch")
async def consultation_batch(
    payload: ConsultationBatchRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    from consultation.evaluate import evaluate_user_for_consultation
    from consultation.roles import is_staff

    if not await is_staff(db, user_id):
        raise HTTPException(status_code=403, detail="Staff role required")
    results = []
    for target in dict.fromkeys(uid.strip() for uid in payload.user_ids if uid and uid.strip()):
        try:
            results.append(
                await evaluate_user_for_consultation(db, target, trigger="BATCH", actor=user_id)
            )
        except Exception:
            logger.exception("Batch evaluation failed for user=%s", target)
            results.append({"user_id": target, "error": "evaluation_failed"})
    return {"count": len(results), "results": results}


@router.get("/consultation-evaluation/status")
async def consultation_status(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """The signed-in user's latest decision. Signals and audit rows stay internal."""
    from consultation.evaluate import latest_evaluation, unread_notifications

    latest = await latest_evaluation(db, user_id)
    return {
        "status": (latest or {}).get("status") or "NOT_EVALUATED",
        "care_recommendation": (latest or {}).get("care_recommendation"),
        "evaluation_timestamp": (latest or {}).get("evaluation_timestamp"),
        "unread_notifications": await unread_notifications(db, user_id),
    }

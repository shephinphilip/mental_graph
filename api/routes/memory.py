"""Personalization memory routes: consent, APM feedback, deletion, consolidation.

NOTE (Phase 3 follow-up): ``delete_memory`` still touches ``meditation_executions``
and ``meditation_offers`` directly. That inline Mongo access is preserved verbatim
here during the Phase 2 move and is slated to go behind a meditation store function.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from database import get_db
from schemas import APMFeedbackRequest, PersonalizationConsentRequest
from services.apm import delete_adaptive_memory, record_intervention_feedback
from services.users import set_personalization_consent

router = APIRouter(tags=["memory"])


@router.post("/memory/consent")
async def update_memory_consent(
    payload: PersonalizationConsentRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    if not await set_personalization_consent(db, user_id, payload.enabled):
        raise HTTPException(status_code=404, detail="Active user not found")
    return {"enabled": payload.enabled}


@router.post("/memory/feedback")
async def intervention_feedback(
    payload: APMFeedbackRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        recorded = await record_intervention_feedback(
            db,
            user_id,
            edge_id=payload.edge_id,
            intervention_id=payload.intervention_id,
            execution_nonce=payload.execution_nonce,
            event_type=payload.event_type,
            before_state=payload.before_state,
            after_state=payload.after_state,
        )
        return {"recorded": recorded}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/memory")
async def delete_memory(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Revoke personalized memory material.

    Deletes APM collections and longitudinal user_patterns / pattern_evidence
    for the authenticated user. Chat history and Graph RAG are retained.
    """
    deleted = await delete_adaptive_memory(db, user_id)
    from services.patterns.store import delete_user_patterns
    from student_memory.store import delete_student_memory

    pattern_deleted = await delete_user_patterns(db, user_id)
    facts_deleted = await delete_student_memory(db, user_id)
    meditation_deleted = await db["meditation_executions"].delete_many({"user_id": user_id})
    await db["meditation_offers"].delete_many({"user_id": user_id})
    return {
        "deleted": deleted,
        "patterns_deleted": pattern_deleted,
        "student_facts_deleted": facts_deleted,
        "meditation_executions_deleted": meditation_deleted.deleted_count,
    }


@router.post("/memory/consolidate")
async def consolidate_memory(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Rebuild the profile summary from stored facts. Also run by scripts/consolidate_memory.py."""
    from student_memory.store import consolidate_student_memory

    return await consolidate_student_memory(db, user_id)

"""Personalization memory routes: consent, APM feedback, deletion, consolidation.

NOTE (Phase 3 follow-up): ``delete_memory`` still touches ``meditation_executions``
and ``meditation_offers`` directly. That inline Mongo access is preserved verbatim
here during the Phase 2 move and is slated to go behind a meditation store function.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from config.config import logger
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
        logger.warning("Memory feedback rejected: %s", exc)
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("Memory feedback rejected: %s", exc)
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
    from services.student_profile import delete_student_profile

    profile_deleted = await delete_student_profile(db, user_id)
    meditation_deleted = await db["meditation_executions"].delete_many({"user_id": user_id})
    await db["meditation_offers"].delete_many({"user_id": user_id})
    return {
        "deleted": deleted,
        "patterns_deleted": pattern_deleted,
        "student_facts_deleted": facts_deleted,
        "student_profile_deleted": profile_deleted,
        "meditation_executions_deleted": meditation_deleted.deleted_count,
    }


@router.post("/memory/consolidate")
async def consolidate_memory(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Rebuild stored facts and the derived student profile for the token owner."""
    from services.student_profile import consolidate_student_profile
    from student_memory.store import consolidate_student_memory

    try:
        facts = await consolidate_student_memory(db, user_id)
        profile = await consolidate_student_profile(db, user_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Active user not found") from exc
    consolidated_at = (profile.get("metadata") or {}).get("last_consolidated_at")
    return {
        "kept": facts.get("kept", 0),
        "archived": facts.get("archived", 0),
        "summary_chars": facts.get("summary_chars", 0),
        "success": True,
        "profile_updated": True,
        "user_id": user_id,
        "profile_version": profile.get("profile_version", 1),
        "last_consolidated_at": consolidated_at.isoformat()
        if hasattr(consolidated_at, "isoformat")
        else consolidated_at,
        "data_completeness": (profile.get("metadata") or {}).get("data_completeness", 0.0),
    }


@router.get("/memory/profile")
async def get_memory_profile(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Sanitized derived profile for the token owner. No transcripts or graph ids."""
    from services.student_profile import read_profile_for_owner

    profile = await read_profile_for_owner(db, user_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No consolidated profile yet")
    return profile

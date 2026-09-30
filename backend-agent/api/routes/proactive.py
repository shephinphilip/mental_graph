"""Proactive question evaluation, pending lookup, and response recording."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import assert_owner, authenticated_user_id
from config.config import logger
from database import get_db
from schemas import (
    ProactiveDecisionResponse,
    ProactiveEvaluateRequest,
    ProactiveRespondRequest,
)
from services.proactive.service import (
    evaluate_proactive_question,
    pending_public,
    record_proactive_response,
)

router = APIRouter(tags=["proactive"])


def _claimed_or_auth(claimed: str | None, authenticated: str) -> str:
    if claimed:
        assert_owner(claimed, authenticated)
        return claimed
    return authenticated


@router.post("/proactive/evaluate", response_model=ProactiveDecisionResponse)
async def evaluate_proactive(
    payload: ProactiveEvaluateRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    owner = _claimed_or_auth(payload.user_id, user_id)
    result = await evaluate_proactive_question(
        db,
        owner,
        session_id=payload.session_id,
        message=payload.message,
        opening_turn=payload.opening_turn,
        dispatch=False,
    )
    return result.public_payload()


@router.get("/proactive/pending")
async def pending_proactive(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return await pending_public(db, user_id)


@router.post("/proactive/respond")
async def respond_proactive(
    payload: ProactiveRespondRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    owner = _claimed_or_auth(payload.user_id, user_id)
    recorded = await record_proactive_response(
        db,
        owner,
        payload.event_id,
        payload.message,
        outcome=payload.outcome,
    )
    if not recorded.get("recorded") and recorded.get("reason") == "not_found":
        logger.warning("Proactive respond missed event user=%s", owner)
        raise HTTPException(status_code=404, detail="Proactive event not found")
    if not recorded.get("recorded") and recorded.get("reason") == "not_delivered":
        raise HTTPException(
            status_code=400, detail="Proactive event was not delivered"
        )
    return {
        "recorded": recorded.get("recorded"),
        "idempotent": recorded.get("idempotent", False),
        "outcome": recorded.get("outcome"),
    }

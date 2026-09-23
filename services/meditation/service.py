"""
Load user-scoped evidence, rank one practice, and record executions.

Ranking itself is pure (``engine.recommend``). This module only fetches
context and writes ``meditation_executions``. APM is updated through the
existing recovery-edge path, and only after explicit helpfulness feedback
when personalization consent is on.
"""

from __future__ import annotations

import inspect
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo.errors import DuplicateKeyError

from meditation.audio import get_audio_path
from meditation.data import get_all_sessions, get_session_by_id
from meditation.metadata import (
    METADATA_STATUS_EMPIRICALLY_VALIDATED,
    METADATA_STATUS_PROVISIONAL,
)
from schemas import APMNodeType, APMRelationType, APMTransition
from services.apm import (
    _upsert_node,
    _upsert_transition,
    make_apm_node_id,
    personalization_enabled,
    record_intervention_feedback,
    temporal_bucket,
)
from services.meditation.engine import (
    MeditationDecision,
    evaluate_catalog_promotion,
    recommend,
)

logger = logging.getLogger(__name__)

EXECUTIONS = "meditation_executions"
OFFERS = "meditation_offers"
PROMOTIONS = "meditation_metadata_promotions"
HELPFULNESS = {"HELPFUL", "NOT_HELPFUL", "DISMISSED"}


async def ensure_meditation_indexes(db) -> None:
    await db[EXECUTIONS].create_index(
        [("user_id", 1), ("execution_nonce", 1)],
        unique=True,
        name="uniq_meditation_execution_nonce",
    )
    await db[EXECUTIONS].create_index(
        [("user_id", 1), ("started_at", -1)],
        name="idx_meditation_execution_recent",
    )
    await db[OFFERS].create_index(
        [("user_id", 1), ("execution_nonce", 1)],
        unique=True,
        name="uniq_meditation_offer_nonce",
    )
    await db[PROMOTIONS].create_index(
        "meditation_id",
        unique=True,
        name="uniq_meditation_promotion",
    )


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


async def _to_list(cursor: Any, limit: int = 40) -> List[Dict[str, Any]]:
    try:
        docs = await cursor.to_list(length=limit)
    except Exception:
        return []
    return _as_list(docs)


async def _find_user(db, user_id: str) -> Dict[str, Any]:
    try:
        doc = await db["users"].find_one({"user_id": user_id})
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


async def _mood_text(db, user_id: str) -> str:
    try:
        cursor = db["mood_logs"].find({"user_id": user_id})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("logged_at", -1)
        docs = await _to_list(cursor, 12)
    except Exception:
        return ""
    lines = []
    for doc in docs:
        bits = [str(doc.get("mood") or ""), str(doc.get("note") or "")]
        line = " ".join(bit for bit in bits if bit).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


async def _executions(db, user_id: str) -> List[Dict[str, Any]]:
    try:
        cursor = db[EXECUTIONS].find({"user_id": user_id})
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("started_at", -1)
        rows = await _to_list(cursor, 40)
    except Exception:
        return []
    enriched = []
    for row in rows:
        item = dict(row)
        if not item.get("technique"):
            session = get_session_by_id(str(item.get("meditation_id") or ""))
            if session:
                item["technique"] = session.get("technique")
        enriched.append(item)
    return enriched


async def _apm_success(db, user_id: str) -> Dict[str, float]:
    """Explicit RECOVERED_BY rates only. A displayed card is not a success."""
    try:
        edges = await _to_list(
            db["apm_edges"].find(
                {"user_id": user_id, "relation_type": APMRelationType.RECOVERED_BY.value}
            ),
            40,
        )
    except Exception:
        return {}
    if not edges:
        return {}
    target_ids = [edge.get("target_node_id") for edge in edges if edge.get("target_node_id")]
    nodes: Dict[str, Dict[str, Any]] = {}
    try:
        found = await _to_list(
            db["apm_nodes"].find({"user_id": user_id, "node_id": {"$in": target_ids}}),
            len(target_ids) or 1,
        )
        nodes = {node.get("node_id"): node for node in found}
    except Exception:
        nodes = {}

    scores: Dict[str, float] = {}
    for edge in edges:
        successes = int(edge.get("explicit_successes") or 0)
        failures = int(edge.get("explicit_failures") or 0)
        if successes + failures <= 0:
            continue
        rate = successes / (successes + failures)
        meditation_id = str(edge.get("meditation_id") or "")
        if not meditation_id:
            label = (nodes.get(edge.get("target_node_id")) or {}).get("canonical_label") or ""
            if label.startswith("meditation:"):
                meditation_id = label.split(":", 1)[1]
        if meditation_id:
            scores[meditation_id] = max(scores.get(meditation_id, 0.0), rate)
    return scores


def _signals_from_user(user: Dict[str, Any]) -> Dict[str, str]:
    def text(*keys: str) -> str:
        parts = [str(user.get(key) or "") for key in keys]
        return "\n".join(part for part in parts if part).strip()

    return {
        "preferred_language": str(user.get("preferred_language") or "en"),
        "sleep_text": text("sleep_summary"),
        "academic_text": text("academic_summary", "academic_data"),
        "attendance_text": text("attendance_summary", "attendance_data"),
        "task_text": text("task_summary"),
        "journal_text": text("journal_summary"),
    }


def _pre_state(decision: MeditationDecision) -> Optional[Dict[str, Any]]:
    estimate = decision.estimate
    if estimate is None:
        return None
    return {
        "valence": round(estimate.valence, 3),
        "arousal": round(estimate.arousal, 3),
        "dominance": round(estimate.dominance, 3),
        "latent_state": estimate.top_state,
        "confidence": round(estimate.confidence, 3),
    }


def _offer_dict(decision: MeditationDecision, nonce: str) -> Dict[str, Any]:
    if decision.decision != "RECOMMEND_MEDITATION" or not decision.session:
        return {
            "decision": "NO_MEDITATION",
            "withheld_reason": decision.withheld_reason,
        }
    session = decision.session
    return {
        "decision": "RECOMMEND_MEDITATION",
        "meditation_id": session["id"],
        "title": session.get("title"),
        "category": session.get("category"),
        "duration_seconds": session.get("duration_seconds"),
        "user_reason": decision.user_reason,
        "execution_nonce": nonce,
        "audio_available": get_audio_path(session["id"]) is not None,
        "friction_level": session.get("friction_level"),
    }


def overlay_promotions(sessions: List[Dict[str, Any]], promoted_ids: set) -> List[Dict[str, Any]]:
    """Leave the source catalog file untouched. Validated ids are overlaid."""
    overlaid = []
    for session in sessions:
        if (
            str(session.get("id")) in promoted_ids
            and session.get("metadata_status") == METADATA_STATUS_PROVISIONAL
        ):
            updated = dict(session)
            updated["metadata_status"] = METADATA_STATUS_EMPIRICALLY_VALIDATED
            overlaid.append(updated)
        else:
            overlaid.append(session)
    return overlaid


async def _promoted_ids(db) -> set:
    try:
        rows = await _to_list(
            db[PROMOTIONS].find({"metadata_status": METADATA_STATUS_EMPIRICALLY_VALIDATED}),
            500,
        )
    except Exception:
        return set()
    return {str(row.get("meditation_id")) for row in rows if row.get("meditation_id")}


async def refresh_catalog_promotion(db, meditation_id: str) -> Optional[str]:
    """
    Recompute catalog status from completed listens and explicit feedback.

    This is global to the session, not a per-user diagnosis. Unlabeled
    completions do not vote.
    """
    try:
        rows = await _to_list(db[EXECUTIONS].find({"meditation_id": str(meditation_id)}), 20000)
    except Exception:
        return None
    completions = helpful = not_helpful = 0
    for row in rows:
        if row.get("status") == "COMPLETED":
            completions += 1
        feedback = (row.get("user_helpfulness_feedback") or "").upper()
        if feedback == "HELPFUL":
            helpful += 1
        elif feedback == "NOT_HELPFUL":
            not_helpful += 1
    status = evaluate_catalog_promotion(completions, helpful, not_helpful)
    now = datetime.now(timezone.utc)
    try:
        pending = db[PROMOTIONS].update_one(
            {"meditation_id": str(meditation_id)},
            {
                "$set": {
                    "meditation_id": str(meditation_id),
                    "metadata_status": status or METADATA_STATUS_PROVISIONAL,
                    "completions": completions,
                    "helpful": helpful,
                    "not_helpful": not_helpful,
                    "updated_at": now,
                }
            },
            upsert=True,
        )
        if inspect.isawaitable(pending):
            await pending
    except Exception:
        logger.exception("Could not store meditation promotion for %s", meditation_id)
    return status


async def _decide(
    db,
    user_id: str,
    message: str,
    *,
    opening_turn: bool = False,
) -> MeditationDecision:
    user = await _find_user(db, user_id)
    signals = _signals_from_user(user)
    sessions = overlay_promotions(get_all_sessions(), await _promoted_ids(db))
    return recommend(
        message,
        preferred_language=signals["preferred_language"],
        mood_text=await _mood_text(db, user_id),
        sleep_text=signals["sleep_text"],
        academic_text=signals["academic_text"],
        attendance_text=signals["attendance_text"],
        task_text=signals["task_text"],
        journal_text=signals["journal_text"],
        executions=await _executions(db, user_id),
        apm_success=await _apm_success(db, user_id),
        opening_turn=opening_turn,
        sessions=sessions,
    )


async def prepare_turn_offer(
    db,
    *,
    user_id: str,
    message: str,
    opening_turn: bool = False,
) -> Dict[str, Any]:
    """Rank at most one practice for this turn. Failures become no offer."""
    try:
        decision = await _decide(db, user_id, message, opening_turn=opening_turn)
    except Exception:
        logger.exception("Meditation ranking failed for user=%s", user_id)
        return {"decision": "NO_MEDITATION", "withheld_reason": "error"}

    nonce = secrets.token_urlsafe(18)
    offer = _offer_dict(decision, nonce)
    offer["prompt_block"] = decision.prompt_block()
    if offer["decision"] != "RECOMMEND_MEDITATION":
        return offer
    try:
        pending = db[OFFERS].update_one(
            {"user_id": user_id, "execution_nonce": nonce},
            {
                "$setOnInsert": {
                    "user_id": user_id,
                    "execution_nonce": nonce,
                    "meditation_id": offer["meditation_id"],
                    "pre_state": _pre_state(decision),
                    "created_at": datetime.now(timezone.utc),
                }
            },
            upsert=True,
        )
        if inspect.isawaitable(pending):
            await pending
    except Exception:
        logger.exception("Could not stash meditation offer for user=%s", user_id)
    return offer


async def preview_for_user(
    db,
    user_id: str,
    message: Optional[str] = None,
) -> Dict[str, Any]:
    """Developer preview. Debug stays on this payload, not on chat cards."""
    user = await _find_user(db, user_id)
    probe = (message or "").strip() or str(user.get("demo_probe_message") or "")
    decision = await _decide(db, user_id, probe)
    offer = _offer_dict(decision, "")
    offer.pop("execution_nonce", None)
    debug: Dict[str, Any] = {"withheld_reason": decision.withheld_reason}
    if decision.estimate is not None:
        debug.update(decision.estimate.as_public_debug())
        debug["friction_level"] = decision.estimate.allowed_friction and sorted(
            decision.estimate.preferred_friction
        )
    debug["cold_start"] = decision.cold_start
    if decision.breakdown is not None:
        debug["winner"] = decision.breakdown.as_dict()
        debug["apm_personal_success"] = decision.breakdown.personal_success
        debug["repetition_penalty"] = decision.breakdown.repetition_penalty
        debug["final_score"] = round(decision.breakdown.final_score, 3)
    debug["top_candidates"] = [row.as_dict() for row in decision.ranked]
    return {
        "probe_message": probe,
        "decision": offer.get("decision"),
        "meditation_id": offer.get("meditation_id"),
        "title": offer.get("title"),
        "category": offer.get("category"),
        "duration_seconds": offer.get("duration_seconds"),
        "reason": offer.get("user_reason") or "",
        "audio_available": offer.get("audio_available", False),
        "friction_level": offer.get("friction_level"),
        "debug": debug,
    }


def _owned_query(user_id: str, execution_id: str, execution_nonce: str) -> Dict[str, Any]:
    return {
        "user_id": user_id,
        "execution_id": execution_id,
        "execution_nonce": execution_nonce,
    }


async def start_execution(
    db,
    *,
    user_id: str,
    meditation_id: str,
    execution_nonce: str,
    session_id: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    session = get_session_by_id(str(meditation_id))
    if session is None:
        raise ValueError("Unknown meditation")
    nonce = (execution_nonce or "").strip()
    if not nonce:
        raise ValueError("execution_nonce is required")

    existing = await db[EXECUTIONS].find_one(
        {"user_id": user_id, "execution_nonce": nonce}
    )
    if isinstance(existing, dict):
        if str(existing.get("meditation_id")) != str(meditation_id):
            raise ValueError("execution_nonce already belongs to another practice")
        return existing

    pre_state = None
    try:
        offer = await db[OFFERS].find_one({"user_id": user_id, "execution_nonce": nonce})
        if isinstance(offer, dict):
            pre_state = offer.get("pre_state")
    except Exception:
        pre_state = None

    now = datetime.now(timezone.utc)
    doc = {
        "execution_id": f"mexe_{secrets.token_hex(8)}",
        "user_id": user_id,
        "session_id": session_id or None,
        "meditation_id": str(meditation_id),
        "technique": session.get("technique"),
        "pre_state": pre_state,
        "status": "STARTED",
        "listen_duration_seconds": 0,
        "user_helpfulness_feedback": None,
        "execution_nonce": nonce,
        "reason": (reason or "")[:500] or None,
        "started_at": now,
        "completed_at": None,
    }
    try:
        await db[EXECUTIONS].insert_one(doc)
    except DuplicateKeyError:
        existing = await db[EXECUTIONS].find_one(
            {"user_id": user_id, "execution_nonce": nonce}
        )
        if not isinstance(existing, dict):
            raise
        if str(existing.get("meditation_id")) != str(meditation_id):
            raise ValueError("execution_nonce already belongs to another practice")
        return existing
    return doc


async def complete_execution(
    db,
    *,
    user_id: str,
    execution_id: str,
    execution_nonce: str,
    listen_duration_seconds: int = 0,
) -> Dict[str, Any]:
    query = _owned_query(user_id, execution_id, execution_nonce)
    existing = await db[EXECUTIONS].find_one(query)
    if not isinstance(existing, dict):
        raise ValueError("Meditation execution not found")
    if existing.get("status") == "COMPLETED":
        return existing
    now = datetime.now(timezone.utc)
    duration = max(0, int(listen_duration_seconds or 0))
    await db[EXECUTIONS].update_one(
        query,
        {
            "$set": {
                "status": "COMPLETED",
                "listen_duration_seconds": duration,
                "completed_at": now,
            }
        },
    )
    existing.update(
        status="COMPLETED",
        listen_duration_seconds=duration,
        completed_at=now,
    )
    try:
        await _mirror_explicit_outcome(db, user_id, existing, "COMPLETED")
    except Exception:
        logger.exception("APM completion event failed for user=%s", user_id)
    try:
        await refresh_catalog_promotion(db, str(existing.get("meditation_id")))
    except Exception:
        logger.exception("Catalog promotion refresh failed")
    return existing


async def record_feedback(
    db,
    *,
    user_id: str,
    execution_id: str,
    execution_nonce: str,
    feedback: str,
) -> Dict[str, Any]:
    event = (feedback or "").upper()
    if event not in HELPFULNESS:
        raise ValueError("Unsupported meditation feedback")
    query = _owned_query(user_id, execution_id, execution_nonce)
    existing = await db[EXECUTIONS].find_one(query)
    if not isinstance(existing, dict):
        raise ValueError("Meditation execution not found")
    if existing.get("user_helpfulness_feedback"):
        return existing

    now = datetime.now(timezone.utc)
    await db[EXECUTIONS].update_one(
        query,
        {"$set": {"user_helpfulness_feedback": event, "feedback_at": now}},
    )
    existing["user_helpfulness_feedback"] = event
    existing["feedback_at"] = now

    if event in {"HELPFUL", "NOT_HELPFUL"}:
        try:
            await _mirror_explicit_outcome(db, user_id, existing, event)
        except Exception:
            logger.exception(
                "APM mirror failed for meditation feedback user=%s", user_id
            )
        try:
            await refresh_catalog_promotion(db, str(existing.get("meditation_id")))
        except Exception:
            logger.exception("Catalog promotion refresh failed")
    return existing


async def _mirror_explicit_outcome(db, user_id: str, execution: Dict[str, Any], event: str) -> None:
    """Write a RECOVERED_BY outcome through the existing APM feedback path."""
    try:
        consented = await personalization_enabled(db, user_id)
    except Exception:
        return
    if consented is not True:
        return

    meditation_id = str(execution.get("meditation_id"))
    latent = ((execution.get("pre_state") or {}) or {}).get("latent_state") or "unspecified state"
    bucket = temporal_bucket()
    label = f"meditation:{meditation_id}"
    await _upsert_node(
        db,
        user_id,
        APMNodeType.LATENT_STATE,
        str(latent),
        confidence_score=0.6,
        bucket=bucket,
    )
    await _upsert_node(
        db,
        user_id,
        APMNodeType.INTERVENTION,
        label,
        aliases=[meditation_id],
        confidence_score=0.7,
        bucket=bucket,
    )
    edge_id = await _upsert_transition(
        db,
        user_id,
        APMTransition(
            source_type=APMNodeType.LATENT_STATE,
            source_label=str(latent),
            target_type=APMNodeType.INTERVENTION,
            target_label=label,
            relation_type=APMRelationType.RECOVERED_BY,
            confidence_score=0.7,
        ),
        bucket=bucket,
    )
    intervention_id = make_apm_node_id(user_id, APMNodeType.INTERVENTION, label)
    await record_intervention_feedback(
        db,
        user_id,
        edge_id=edge_id,
        intervention_id=intervention_id,
        execution_nonce=execution["execution_nonce"],
        event_type=event,
    )

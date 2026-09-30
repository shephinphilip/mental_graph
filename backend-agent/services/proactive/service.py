"""Orchestrate the proactive question pipeline. Default is do nothing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Sequence

from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import logger
from services.apm import contains_crisis_signal, persist_apm_extraction
from services.inner_council import CouncilStance
from services.language_preferences import resolve_response_language
from services.proactive.eligibility import eligibility_reason, personalization_allowed
from services.proactive.inner_council import review_candidate
from services.proactive.observability import Timer, bump, log_decision
from services.proactive.question_generator import generate_candidate, soften_once
from core.logging import hash_user_id
from services.proactive.receptivity import assess_receptivity, permits_proactive
from services.proactive.schemas import (
    Decision,
    ProactiveResult,
    ProactiveStatus,
    QuestionCandidate,
    ReceptivityState,
    no_question,
)
from services.proactive.store import (
    cooldown_reason,
    get_event,
    insert_opportunity,
    latest_awaiting_response,
    make_event_id,
    make_execution_nonce,
    mark_status,
    opened_question,
    pending_for_user,
)
from services.proactive.trigger_engine import (
    choose_trigger,
    current_turn_overlaps_topic,
    detect_divergence,
    retrieve_bounded_context,
)
from services.proactive.validator import validate_candidate
from services.safety_class import SafetyClass, classify_message
from schemas import APMExtraction, APMNodeType, APMObservation

_EXPLICIT_HELPFUL = (
    "it helped",
    "that helped",
    "felt better",
    "feeling better",
    "eased up",
    "lighter",
    "more manageable",
)
_EXPLICIT_UNHELPFUL = (
    "didn't help",
    "did not help",
    "made it worse",
    "still stuck",
)
_IGNORED = ("k", "ok", "okay", "idk", "whatever", "sure")


def stance_with_proactive(brief: str, question: str) -> str:
    return (
        f"{brief}\n"
        "• Proactive question (silent — never mention this label): "
        "If this turn includes a question, use this single approved question, "
        "adapted to the selected language and script. Do not add another. "
        "Do not mention graphs, scores, telemetry, or memory systems.\n"
        f"  Approved question: {question}"
    )


async def evaluate_proactive_question(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    session_id: str = "",
    message: str = "",
    opening_turn: bool = False,
    message_history: Sequence[dict] | None = None,
    user_context: Optional[Dict[str, Any]] = None,
    risk_intensity: float = 1.0,
    persistent_distress: bool = False,
    council: Optional[CouncilStance] = None,
    dispatch: bool = False,
    now: Optional[datetime] = None,
    graph_context: str = "",
) -> ProactiveResult:
    timer = Timer()
    bump("candidates_evaluated")
    moment = now or datetime.now(timezone.utc)
    context = user_context or {}

    try:
        gate = await eligibility_reason(
            db,
            user_id,
            message,
            risk_intensity=risk_intensity,
            persistent_distress=persistent_distress,
            council=council,
        )
    except Exception:
        logger.exception("Proactive eligibility failed user=%s", user_id)
        return _finish(user_id, no_question("eligibility_error"), timer)

    if gate:
        if gate.startswith("safety_"):
            bump("safety_suppression_count")
        bump("candidates_suppressed")
        return _finish(user_id, no_question(gate), timer)

    content = "" if opening_turn else (message or "")

    try:
        consented = await personalization_allowed(db, user_id)
    except Exception:
        consented = False
    if not consented:
        bump("candidates_suppressed")
        return _finish(user_id, no_question("consent_required"), timer)

    receptivity = assess_receptivity(
        content,
        opening_turn=opening_turn,
        journal_context=str(context.get("journal_context") or ""),
        message_history=message_history,
    )
    if receptivity is ReceptivityState.HIGH_OVERLOAD:
        bump("candidates_suppressed")
        return _finish(
            user_id,
            no_question("high_overload", receptivity=receptivity.value),
            timer,
        )
    if receptivity is ReceptivityState.LOW_RECEPTIVITY and not opening_turn:
        bump("candidates_suppressed")
        return _finish(
            user_id,
            no_question("low_receptivity", receptivity=receptivity.value),
            timer,
        )
    if not permits_proactive(receptivity) and not (
        opening_turn and receptivity is ReceptivityState.UNKNOWN
    ):
        bump("candidates_suppressed")
        return _finish(
            user_id,
            no_question("low_receptivity", receptivity=receptivity.value),
            timer,
        )

    try:
        language = await resolve_response_language(
            db,
            user_id,
            current_message="" if opening_turn else content,
            opening_turn=opening_turn,
        )
    except Exception:
        language = {
            "resolved_language": context.get("preferred_language") or "ENGLISH",
            "resolved_script": context.get("response_script") or "LATIN",
        }
    lang_code = str(language.get("resolved_language") or "ENGLISH")
    script = str(language.get("resolved_script") or "LATIN")

    try:
        retrieval_timer = Timer()
        bundle = await retrieve_bounded_context(
            db,
            user_id,
            content,
            now=moment,
            graph_context=graph_context or str(context.get("graph_context") or ""),
            adaptive_memory_context=str(context.get("adaptive_memory_context") or ""),
        )
        retrieval_ms = retrieval_timer.ms()
    except Exception:
        logger.exception("Proactive graph retrieve failed user=%s", user_id)
        bundle = {
            "nodes": [],
            "edges": [],
            "recovery": [],
            "empty": True,
            "existing_graph_context_available": False,
            "existing_apm_context_available": False,
            "proactive_additional_graph_query": False,
            "proactive_additional_apm_query": False,
        }
        retrieval_ms = 0.0

    if bundle.get("empty"):
        bump("candidates_suppressed")
        return _finish(
            user_id,
            no_question(
                "empty_graph",
                receptivity=receptivity.value,
                existing_graph_context_available=bool(
                    bundle.get("existing_graph_context_available")
                ),
                existing_apm_context_available=bool(
                    bundle.get("existing_apm_context_available")
                ),
                proactive_additional_graph_query=bool(
                    bundle.get("proactive_additional_graph_query")
                ),
                proactive_additional_apm_query=bool(
                    bundle.get("proactive_additional_apm_query")
                ),
                proactive_retrieval_latency_ms=retrieval_ms,
            ),
            timer,
        )

    divergence = detect_divergence(content, bundle, opening_turn=opening_turn)
    trigger_type, topic_node = choose_trigger(
        content,
        bundle,
        divergence,
        opening_turn=opening_turn,
        journal_context=str(context.get("journal_context") or ""),
        sleep_context=str(context.get("sleep_context") or ""),
        habit_context=str(context.get("active_habits") or ""),
        academic_context=str(context.get("academic_context") or ""),
    )
    if not trigger_type or not topic_node.label:
        bump("candidates_suppressed")
        return _finish(
            user_id,
            no_question("no_justified_trigger", receptivity=receptivity.value),
            timer,
        )
    if (
        not opening_turn
        and not divergence.detected
        and current_turn_overlaps_topic(content, topic_node.label)
    ):
        bump("candidates_suppressed")
        return _finish(
            user_id,
            no_question("already_in_conversation", receptivity=receptivity.value),
            timer,
        )

    confidence = float(topic_node.confidence or 0.4)
    if receptivity is ReceptivityState.UNKNOWN:
        confidence = min(confidence, 0.45)
    if divergence.detected and divergence.confidence == "low":
        confidence = min(confidence, 0.5)

    age = await _user_age(db, user_id)
    candidate = generate_candidate(
        trigger_type=trigger_type,
        topic_node=topic_node,
        receptivity=receptivity,
        confidence=confidence,
        language=lang_code,
        script=script,
        risk_state="none",
        age=age,
        opening_turn=opening_turn,
        reason=divergence.reason or trigger_type.lower(),
    )
    if candidate is None:
        bump("candidates_suppressed")
        return _finish(user_id, no_question("no_candidate"), timer)

    council_decision = review_candidate(
        candidate,
        content,
        message_history,
        opening_turn=opening_turn,
        council=council,
        risk_intensity=risk_intensity,
        persistent_distress=persistent_distress,
    )
    if not council_decision.allowed:
        bump("candidates_suppressed")
        bump("validation_rejections")
        return _finish(
            user_id,
            no_question(council_decision.reason or "council_rejected"),
            timer,
        )

    validated, reject_reason = validate_candidate(candidate)
    if validated is None:
        bump("validation_rejections")
        bumped = _retry_soften(candidate)
        if bumped is not None:
            validated, reject_reason = validate_candidate(bumped, retry=True)
        if validated is None:
            bump("candidates_suppressed")
            return _finish(
                user_id,
                no_question(reject_reason or "validator_reject"),
                timer,
            )
    candidate = validated

    if not dispatch:
        from services.proactive.localize import localize_question

        localized, loc_reason = await localize_question(candidate)
        if localized is None:
            bump("candidates_suppressed")
            bump("validation_rejections")
            return _finish(user_id, no_question(loc_reason or "localization_failed"), timer)
        candidate = localized

    cool = await cooldown_reason(
        db, user_id, topic=candidate.topic, question=candidate.question, now=moment
    )
    if cool:
        bump("candidates_suppressed")
        if cool.startswith("duplicate"):
            bump("duplicate_attempts_suppressed")
        return _finish(user_id, no_question(cool, receptivity=receptivity.value), timer)

    nonce = make_execution_nonce(
        user_id, candidate.trigger_type, candidate.topic, now=moment
    )
    event_id = make_event_id(user_id, nonce)
    status = (
        ProactiveStatus.DISPATCHED.value if dispatch else ProactiveStatus.APPROVED.value
    )
    stored = await insert_opportunity(
        db,
        user_id=user_id,
        event_id=event_id,
        execution_nonce=nonce,
        trigger_type=candidate.trigger_type,
        question=candidate.question,
        receptivity_state=receptivity.value,
        confidence=candidate.confidence,
        risk_state=candidate.risk_state,
        language=candidate.language,
        script=candidate.script,
        source_node_ids=candidate.source_nodes,
        status=status,
        session_id=session_id,
        topic=candidate.topic,
        now=moment,
    )
    if stored.get("duplicate"):
        bump("duplicate_attempts_suppressed")
        existing = stored.get("doc") or {}
        existing_status = str(existing.get("status") or "")
        if existing_status in {
            ProactiveStatus.DISPATCHED.value,
            ProactiveStatus.DELIVERED.value,
            ProactiveStatus.RESPONDED.value,
        }:
            bump("candidates_suppressed")
            return _finish(
                user_id,
                no_question("duplicate_event", event_id=str(existing.get("event_id") or event_id)),
                timer,
            )
        event_id = str(existing.get("event_id") or event_id)
        if dispatch and existing_status in {
            ProactiveStatus.APPROVED.value,
            ProactiveStatus.SUPPRESSED.value,
        }:
            await mark_status(db, user_id, event_id, ProactiveStatus.DISPATCHED.value, now=moment)
            status = ProactiveStatus.DISPATCHED.value
        else:
            status = existing_status or status
            if not dispatch:
                bump("questions_approved")
                result = ProactiveResult(
                    decision=Decision.PROACTIVE_QUESTION.value,
                    reason="approved",
                    event_id=event_id,
                    question=opened_question(existing) or candidate.question,
                    status=status,
                    trigger_type=candidate.trigger_type,
                    receptivity=receptivity.value,
                    execution_nonce=nonce,
                    confidence=candidate.confidence,
                    language=candidate.language,
                    script=candidate.script,
                    topic=candidate.topic,
                    existing_graph_context_available=bool(
                        bundle.get("existing_graph_context_available")
                    ),
                    existing_apm_context_available=bool(
                        bundle.get("existing_apm_context_available")
                    ),
                    proactive_additional_graph_query=bool(
                        bundle.get("proactive_additional_graph_query")
                    ),
                    proactive_additional_apm_query=bool(
                        bundle.get("proactive_additional_apm_query")
                    ),
                    proactive_retrieval_latency_ms=retrieval_ms,
                )
                return _finish(user_id, result, timer)

    bump("questions_approved")
    if status == ProactiveStatus.DISPATCHED.value:
        bump("questions_dispatched")
    result = ProactiveResult(
        decision=Decision.PROACTIVE_QUESTION.value,
        reason="approved",
        event_id=event_id,
        question=candidate.question,
        status=status,
        trigger_type=candidate.trigger_type,
        receptivity=receptivity.value,
        execution_nonce=nonce,
        confidence=candidate.confidence,
        language=candidate.language,
        script=candidate.script,
        source_node_ids=list(candidate.source_nodes),
        topic=candidate.topic,
        existing_graph_context_available=bool(
            bundle.get("existing_graph_context_available")
        ),
        existing_apm_context_available=bool(
            bundle.get("existing_apm_context_available")
        ),
        proactive_additional_graph_query=bool(
            bundle.get("proactive_additional_graph_query")
        ),
        proactive_additional_apm_query=bool(
            bundle.get("proactive_additional_apm_query")
        ),
        proactive_retrieval_latency_ms=retrieval_ms,
    )
    return _finish(user_id, result, timer)


async def evaluate_for_chat_turn(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    user_message: str,
    opening_turn: bool,
    message_history: Sequence[dict] | None,
    user_context: Dict[str, Any],
    risk_intensity: float,
    persistent_distress: bool,
    council: CouncilStance,
    graph_context: str = "",
) -> ProactiveResult:
    try:
        await maybe_record_chat_reply(
            db,
            user_id=user_id,
            session_id=session_id,
            message=user_message,
            opening_turn=opening_turn,
        )
        return await evaluate_proactive_question(
            db,
            user_id,
            session_id=session_id,
            message=user_message,
            opening_turn=opening_turn,
            message_history=message_history,
            user_context=user_context,
            risk_intensity=risk_intensity,
            persistent_distress=persistent_distress,
            council=council,
            dispatch=True,
            graph_context=graph_context,
        )
    except Exception:
        logger.exception("Proactive chat evaluation skipped user=%s", user_id)
        return no_question("evaluation_failed")


async def mark_delivered(
    db: AsyncIOMotorDatabase,
    user_id: str,
    event_id: str,
    *,
    message_id: str = "",
    session_id: str = "",
) -> bool:
    if not event_id or not user_id:
        return False
    try:
        from services.proactive.delivery import commit_delivery

        return await commit_delivery(
            db, user_id, event_id, message_id=message_id, session_id=session_id
        )
    except Exception:
        logger.info("Proactive delivered mark skipped user=%s", user_id)
        return False


async def pending_public(
    db: AsyncIOMotorDatabase, user_id: str
) -> Dict[str, Any]:
    doc = await pending_for_user(db, user_id)
    if not doc:
        return no_question("none_pending").public_payload()
    return {
        "decision": Decision.PROACTIVE_QUESTION.value,
        "event_id": doc.get("event_id"),
        "question": opened_question(doc),
        "status": doc.get("status"),
    }


async def record_proactive_response(
    db: AsyncIOMotorDatabase,
    user_id: str,
    event_id: str,
    message: str,
    *,
    outcome: str = "acknowledged",
) -> Dict[str, Any]:
    doc = await get_event(db, user_id, event_id)
    if not doc:
        return {"recorded": False, "reason": "not_found"}
    if doc.get("user_id") != user_id:
        return {"recorded": False, "reason": "not_found"}
    if doc.get("status") == ProactiveStatus.RESPONDED.value:
        return {"recorded": True, "idempotent": True, "outcome": doc.get("outcome")}
    if doc.get("status") not in {
        ProactiveStatus.DELIVERED.value,
        ProactiveStatus.SEEN.value,
    }:
        return {"recorded": False, "reason": "not_delivered"}

    label = classify_message(message or "")
    if label is not SafetyClass.NONE or contains_crisis_signal(message or ""):
        await mark_status(
            db,
            user_id,
            event_id,
            ProactiveStatus.RESPONDED.value,
            extra={"outcome": "crisis_excluded", "evidence_kind": "none"},
        )
        bump("questions_responded")
        return {"recorded": True, "outcome": "crisis_excluded", "graph_updated": False}

    inferred = _infer_outcome(message, outcome)
    extra = {
        "outcome": inferred,
        "evidence_kind": _evidence_kind(inferred, message),
    }
    await mark_status(
        db,
        user_id,
        event_id,
        ProactiveStatus.RESPONDED.value,
        extra=extra,
    )
    bump("questions_responded")
    graph_updated = False
    if inferred == "explicit_helpful" and await personalization_allowed(db, user_id):
        graph_updated = await _record_clarifying_observation(
            db, user_id, doc, message, helpful=True
        )
    elif inferred in {"clarification", "acknowledged"} and await personalization_allowed(
        db, user_id
    ):
        graph_updated = await _record_clarifying_observation(
            db, user_id, doc, message, helpful=False
        )
    return {
        "recorded": True,
        "idempotent": False,
        "outcome": inferred,
        "graph_updated": graph_updated,
    }


async def maybe_record_chat_reply(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    message: str,
    opening_turn: bool,
) -> None:
    if opening_turn or not (message or "").strip():
        return
    pending = await latest_awaiting_response(db, user_id, session_id)
    if not pending:
        return
    await record_proactive_response(
        db,
        user_id,
        str(pending.get("event_id") or ""),
        message,
        outcome="acknowledged",
    )


def _infer_outcome(message: str, requested: str) -> str:
    requested = (requested or "acknowledged").casefold()
    if requested in {"ignored", "helpful", "not_helpful", "clarification", "acknowledged"}:
        mapped = {
            "helpful": "explicit_helpful",
            "not_helpful": "explicit_unhelpful",
        }.get(requested, requested)
        if mapped != "acknowledged":
            return mapped
    text = (message or "").strip().casefold()
    if not text or text in _IGNORED or (len(text.split()) <= 2 and text in _IGNORED):
        if text in _IGNORED:
            return "ignored"
    if any(term in text for term in _EXPLICIT_HELPFUL):
        return "explicit_helpful"
    if any(term in text for term in _EXPLICIT_UNHELPFUL):
        return "explicit_unhelpful"
    if len(text.split()) >= 6:
        return "clarification"
    return "acknowledged"


def _evidence_kind(outcome: str, message: str) -> str:
    if outcome == "explicit_helpful":
        return "outcome"
    if outcome == "clarification":
        return "clarification"
    if outcome == "ignored":
        return "none"
    if outcome == "crisis_excluded":
        return "none"
    if (message or "").strip():
        return "context"
    return "none"


async def _record_clarifying_observation(
    db: AsyncIOMotorDatabase,
    user_id: str,
    event: Dict[str, Any],
    message: str,
    *,
    helpful: bool,
) -> bool:
    """Write a CONTEXT observation. Never invent a RECOVERED_BY edge."""
    if contains_crisis_signal(message):
        return False
    if helpful:
        # User reported relief. Store an OUTCOME label; do not claim a tool helped.
        extraction = APMExtraction(
            observations=[
                APMObservation(
                    node_type=APMNodeType.OUTCOME,
                    label="user reported easing",
                    confidence_score=0.55,
                )
            ]
        )
    else:
        extraction = APMExtraction(
            observations=[
                APMObservation(
                    node_type=APMNodeType.CONTEXT,
                    label="proactive follow-up reply",
                    confidence_score=0.4,
                )
            ]
        )
    try:
        written = await persist_apm_extraction(
            db, user_id, str(event.get("session_id") or ""), extraction
        )
        return written > 0
    except Exception:
        logger.info("Proactive APM outcome write skipped user=%s", user_id)
        return False


async def _user_age(db: AsyncIOMotorDatabase, user_id: str) -> Optional[int]:
    try:
        user = await db["users"].find_one({"user_id": user_id}, {"age": 1})
    except Exception:
        return None
    if not user:
        return None
    age = user.get("age")
    try:
        return int(age) if age is not None else None
    except (TypeError, ValueError):
        return None


def _retry_soften(candidate: QuestionCandidate) -> Optional[QuestionCandidate]:
    softened = soften_once(candidate.question)
    if softened == candidate.question:
        return None
    candidate.question = softened
    return candidate


def _finish(user_id: str, result: ProactiveResult, timer: Timer) -> ProactiveResult:
    log_decision(
        user_id=user_id,
        decision=result.decision,
        trigger_type=result.trigger_type,
        suppression_reason=""
        if result.decision == Decision.PROACTIVE_QUESTION.value
        else result.reason,
        event_id=result.event_id,
        status=result.status,
        latency_ms=timer.ms(),
    )
    logger.info(
        "proactive_retrieval user=%s existing_graph=%s existing_apm=%s "
        "extra_graph=%s extra_apm=%s retrieval_ms=%.1f total_ms=%.1f",
        hash_user_id(user_id),
        int(result.existing_graph_context_available),
        int(result.existing_apm_context_available),
        int(result.proactive_additional_graph_query),
        int(result.proactive_additional_apm_query),
        result.proactive_retrieval_latency_ms,
        timer.ms(),
    )
    return result

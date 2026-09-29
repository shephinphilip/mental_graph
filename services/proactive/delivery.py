"""Final-response enforcement and delivery linkage for proactive turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from config.config import get_settings, logger
from services.proactive.observability import Timer, bump, log_decision
from services.proactive.schemas import ProactiveStatus
from services.proactive.store import get_event, mark_status
from services.proactive.validator import validate_final_response
from services.telemetry import register_metric

RewriteFn = Callable[[str], Awaitable[str]]


@dataclass
class FinalProactiveTurn:
    text: str
    delivered: bool
    reason: str = ""
    retries: int = 0
    used_ordinary_fallback: bool = False


async def ensure_final_proactive_reply(
    text: str,
    *,
    question: str,
    topic: str,
    script: str,
    language: str,
    user_id: str,
    event_id: str,
    pattern_supplied: bool = False,
    rewrite: Optional[RewriteFn] = None,
    ordinary_fallback: Optional[RewriteFn] = None,
) -> FinalProactiveTurn:
    """Validate the generated assistant reply before the user can see it."""
    register_metric("proactive_validation")
    timer = Timer()
    settings = get_settings()
    max_retries = max(0, int(settings.PROACTIVE_FINAL_MAX_RETRIES))
    current = text or ""
    retries = 0
    verdict = validate_final_response(
        current,
        topic=topic,
        question=question,
        script=script,
        language=language,
        pattern_supplied=pattern_supplied,
    )
    if not verdict.ok and rewrite is not None and retries < max_retries:
        register_metric("proactive_retry")
        bump("validation_rejections")
        retries += 1
        instruction = (
            "Rewrite the previous reply. Keep the same topic and one gentle "
            f"question ({verdict.reason}). Do not add another question. "
            "Do not diagnose, pressure, or mention graphs, scores, or telemetry."
        )
        try:
            current = await rewrite(instruction)
        except Exception:
            logger.info("Proactive final rewrite failed event=%s", event_id)
            current = current
        verdict = validate_final_response(
            current,
            topic=topic,
            question=question,
            script=script,
            language=language,
            pattern_supplied=pattern_supplied,
        )

    log_decision(
        user_id=user_id,
        decision="PROACTIVE_FINAL",
        suppression_reason="" if verdict.ok else verdict.reason,
        event_id=event_id,
        status="pass" if verdict.ok else "fail",
        latency_ms=timer.ms(),
    )
    logger.info(
        "proactive_final event_id=%s validation=%s suppression=%s retries=%s latency_ms=%.1f",
        event_id or "-",
        "pass" if verdict.ok else "fail",
        verdict.reason if not verdict.ok else "-",
        retries,
        timer.ms(),
    )
    if verdict.ok:
        return FinalProactiveTurn(text=current, delivered=True, retries=retries)

    bump("validation_rejections")
    fallback = current
    used_ordinary = False
    if ordinary_fallback is not None:
        try:
            fallback = await ordinary_fallback(
                "Reply without a proactive follow-up. Listen and stay with "
                "what they said. At most one question, only if it is about "
                "this turn. Do not recall extra history."
            )
            used_ordinary = True
        except Exception:
            from services.response_validator import FALLBACK_REPLY

            fallback = FALLBACK_REPLY
    else:
        from services.response_validator import FALLBACK_REPLY

        fallback = FALLBACK_REPLY
    return FinalProactiveTurn(
        text=fallback,
        delivered=False,
        reason=verdict.reason or "final_validation_failed",
        retries=retries,
        used_ordinary_fallback=used_ordinary,
    )


async def suppress_undelivered(
    db,
    user_id: str,
    event_id: str,
    reason: str,
) -> None:
    if not event_id or not user_id:
        return
    await mark_status(
        db,
        user_id,
        event_id,
        ProactiveStatus.SUPPRESSED.value,
        extra={"suppression_reason": reason, "delivered_message_id": ""},
    )


async def commit_delivery(
    db,
    user_id: str,
    event_id: str,
    *,
    message_id: str,
    session_id: str = "",
) -> bool:
    """DELIVERED only with a real assistant message_id after validation."""
    if not user_id or not event_id or not (message_id or "").strip():
        return False
    existing = await get_event(db, user_id, event_id)
    if not existing:
        return False
    status = str(existing.get("status") or "")
    if status == ProactiveStatus.DELIVERED.value:
        prior = str(existing.get("delivered_message_id") or "")
        if prior == message_id:
            return True
        if not prior:
            extra = {"delivered_message_id": message_id}
            if session_id:
                extra["session_id"] = session_id
            return await mark_status(
                db,
                user_id,
                event_id,
                ProactiveStatus.DELIVERED.value,
                extra=extra,
            )
        return False
    if status not in {
        ProactiveStatus.DISPATCHED.value,
        ProactiveStatus.APPROVED.value,
    }:
        return False
    extra = {"delivered_message_id": message_id}
    if session_id:
        extra["session_id"] = session_id
    return await mark_status(
        db,
        user_id,
        event_id,
        ProactiveStatus.DELIVERED.value,
        extra=extra,
    )

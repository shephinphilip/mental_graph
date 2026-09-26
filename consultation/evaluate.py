"""One decision per run: REFERRED, MONITORING, or NOT_NEEDED."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import get_settings
from consultation.indexes import AUDIT, EVALUATIONS, NOTIFICATIONS
from consultation.signals import collect_signals

logger = logging.getLogger(__name__)

DECISIONS = ("REFERRED", "MONITORING", "NOT_NEEDED")

RECOMMENDATIONS = {
    "REFERRED": "A conversation with a licensed professional is recommended.",
    "MONITORING": "Not referral-eligible right now. Keep observing.",
    "NOT_NEEDED": "No indication of need for professional consultation.",
}

NOTIFICATION_TYPE = "CONSULTATION_RECOMMENDED"


def decide(signals: Dict[str, Any]) -> Dict[str, Any]:
    """Pure rule. Several weak signals do not add up to a referral on their own."""
    settings = get_settings()
    reasons: List[str] = []
    if signals.get("crisis_flag_recent"):
        reasons.append("crisis flag set within the lookback window")
    if signals.get("report_crisis_signal"):
        reasons.append("a recent session report carried a crisis signal")
    if signals.get("persistent_distress"):
        reasons.append("established persistent distress pattern is active")
    if signals.get("elevated_reports", 0) >= settings.CONSULTATION_MIN_ELEVATED_REPORTS:
        reasons.append(
            f"{signals['elevated_reports']} of the last {signals.get('reports_considered', 0)} "
            f"reports at or above load {settings.CONSULTATION_REPORT_METRIC_THRESHOLD}"
        )
    if (
        signals.get("risk_turns", 0) >= settings.CONSULTATION_MIN_RISK_TURNS
        and signals.get("risk_average", 0.0) >= settings.RISK_HIGH_THRESHOLD
    ):
        reasons.append(
            f"average turn risk {signals['risk_average']} across {signals['risk_turns']} turns"
        )
    if reasons:
        return {"status": "REFERRED", "reasons": reasons}

    watch: List[str] = []
    if signals.get("elevated_reports", 0) >= 1:
        watch.append("one recent report at elevated load")
    if (
        signals.get("risk_turns", 0) >= 3
        and signals.get("risk_average", 0.0) >= settings.RISK_HIGH_THRESHOLD - 2
    ):
        watch.append(f"average turn risk {signals['risk_average']} is raised")
    if watch:
        return {"status": "MONITORING", "reasons": watch}
    return {"status": "NOT_NEEDED", "reasons": []}


async def _audit(db, user_id: str, action: str, *, actor: str, details: Dict[str, Any]) -> None:
    try:
        await db[AUDIT].insert_one(
            {
                "userId": user_id,
                "action": action,
                "timestamp": datetime.now(timezone.utc),
                "actor": actor,
                "details": details,
            }
        )
    except Exception:
        logger.exception("Consultation audit write failed user=%s action=%s", user_id, action)


async def _on_cooldown(db, user_id: str, now: datetime) -> Optional[datetime]:
    settings = get_settings()
    cutoff = now - timedelta(days=settings.CONSULTATION_COOLDOWN_DAYS)
    doc = await db[EVALUATIONS].find_one(
        {"userId": user_id, "status": "REFERRED", "notification_written": True},
        sort=[("evaluation_timestamp", -1)],
    )
    if not doc:
        return None
    when = doc.get("evaluation_timestamp")
    if isinstance(when, datetime):
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        if when >= cutoff:
            return when
    return None


async def _record(
    db,
    *,
    user_id: str,
    status: str,
    reasons: List[str],
    signals: Dict[str, Any],
    trigger: str,
    actor: str,
    session_id: Optional[str],
    now: datetime,
) -> Dict[str, Any]:
    evaluation_id = f"eval_{uuid.uuid4().hex[:12]}"
    cooldown_until: Optional[datetime] = None
    notification_written = False
    if status == "REFERRED":
        last = await _on_cooldown(db, user_id, now)
        if last:
            cooldown_until = last + timedelta(days=get_settings().CONSULTATION_COOLDOWN_DAYS)
        else:
            try:
                await db[NOTIFICATIONS].insert_one(
                    {
                        "userId": user_id,
                        "notification_type": NOTIFICATION_TYPE,
                        "read": False,
                        "payload": {
                            "evaluation_id": evaluation_id,
                            "care_recommendation": RECOMMENDATIONS[status],
                            "reasons": reasons,
                            "session_id": session_id,
                        },
                        "created_at": now,
                    }
                )
                notification_written = True
            except Exception:
                logger.exception("Consultation notification write failed user=%s", user_id)
    doc = {
        "evaluation_id": evaluation_id,
        "userId": user_id,
        "evaluation_timestamp": now,
        "status": status,
        "care_recommendation": RECOMMENDATIONS[status],
        "reasons": reasons,
        "signals": signals,
        "trigger": trigger,
        "actor": actor,
        "session_id": session_id,
        "cooldown_active": cooldown_until is not None,
        "cooldown_until": cooldown_until,
        "notification_written": notification_written,
    }
    await db[EVALUATIONS].insert_one(doc)
    await _audit(
        db,
        user_id,
        "EVALUATION_RUN",
        actor=actor,
        details={
            "evaluation_id": evaluation_id,
            "status": status,
            "trigger": trigger,
            "reasons": reasons,
        },
    )
    if status == "REFERRED":
        await _audit(
            db,
            user_id,
            "NOTIFICATION_WRITTEN" if notification_written else "REFERRAL_SUPPRESSED_COOLDOWN",
            actor=actor,
            details={"evaluation_id": evaluation_id, "cooldown_until": cooldown_until},
        )
    return public_evaluation(doc)


def public_evaluation(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evaluation_id": doc.get("evaluation_id"),
        "user_id": doc.get("userId"),
        "status": doc.get("status"),
        "care_recommendation": doc.get("care_recommendation"),
        "reasons": list(doc.get("reasons") or []),
        "trigger": doc.get("trigger"),
        "evaluation_timestamp": doc.get("evaluation_timestamp"),
        "cooldown_active": bool(doc.get("cooldown_active")),
        "cooldown_until": doc.get("cooldown_until"),
        "notification_written": bool(doc.get("notification_written")),
    }


async def evaluate_user_for_consultation(
    db,
    user_id: str,
    *,
    trigger: str = "REPORT",
    actor: Optional[str] = None,
    session_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run the rule, store the result, notify once per cooldown window."""
    now = now or datetime.now(timezone.utc)
    signals = await collect_signals(db, user_id, now=now)
    verdict = decide(signals)
    return await _record(
        db,
        user_id=user_id,
        status=verdict["status"],
        reasons=verdict["reasons"],
        signals=signals,
        trigger=trigger,
        actor=actor or user_id,
        session_id=session_id,
        now=now,
    )


async def manual_override(
    db,
    user_id: str,
    *,
    status: str,
    reason: str,
    actor: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """A staff member sets the decision. The reason is kept in the audit row."""
    chosen = (status or "").strip().upper()
    if chosen not in DECISIONS:
        raise ValueError("Unsupported status")
    text = (reason or "").strip()
    if len(text) < 5:
        raise ValueError("A reason is required")
    now = now or datetime.now(timezone.utc)
    signals = await collect_signals(db, user_id, now=now)
    result = await _record(
        db,
        user_id=user_id,
        status=chosen,
        reasons=[f"manual override: {text}"],
        signals=signals,
        trigger="MANUAL_OVERRIDE",
        actor=actor,
        session_id=None,
        now=now,
    )
    await _audit(
        db,
        user_id,
        "MANUAL_OVERRIDE",
        actor=actor,
        details={"evaluation_id": result["evaluation_id"], "status": chosen, "reason": text},
    )
    return result


async def latest_evaluation(db, user_id: str) -> Optional[Dict[str, Any]]:
    doc = await db[EVALUATIONS].find_one(
        {"userId": user_id}, sort=[("evaluation_timestamp", -1)]
    )
    return public_evaluation(doc) if doc else None


async def unread_notifications(db, user_id: str) -> int:
    try:
        cursor = db[NOTIFICATIONS].find({"userId": user_id, "read": False}).sort("created_at", -1).limit(20)
        rows = await cursor.to_list(length=20)
    except Exception:
        logger.exception("Could not count notifications user=%s", user_id)
        return 0
    return len(rows)


async def evaluate_after_report(db, user_id: str, session_id: str) -> None:
    """Report hook. Never raises into the report."""
    try:
        await evaluate_user_for_consultation(
            db, user_id, trigger="REPORT", actor="system", session_id=session_id
        )
    except Exception:
        logger.exception("Post-report consultation evaluation failed user=%s", user_id)

"""Read-only view of signals other modules already store."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from config import get_settings
from schemas import PatternStatus

logger = logging.getLogger(__name__)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _within(value: Any, cutoff: datetime) -> bool:
    when = _as_dt(value)
    return bool(when and when >= cutoff)


async def _reports(db, user_id: str, limit: int) -> List[Dict[str, Any]]:
    try:
        from reports.store import recent_reports

        return await recent_reports(db, user_id, limit=limit)
    except Exception:
        logger.exception("Consultation could not read reports for user=%s", user_id)
        return []


async def _persistent_distress(db, user_id: str, cutoff: datetime) -> bool:
    try:
        doc = await db["user_patterns"].find_one(
            {
                "user_id": user_id,
                "status": PatternStatus.ESTABLISHED_PERSISTENT_DISTRESS.value,
            }
        )
    except Exception:
        logger.exception("Consultation could not read patterns for user=%s", user_id)
        return False
    return bool(doc and _within(doc.get("last_observed_at"), cutoff))


async def _risk_turns(db, user_id: str, cutoff: datetime) -> Dict[str, Any]:
    try:
        cursor = db["user_risk_turns"].find({"user_id": user_id}).sort("created_at", -1).limit(40)
        rows = await cursor.to_list(length=40)
    except Exception:
        logger.exception("Consultation could not read risk turns for user=%s", user_id)
        rows = []
    scores = [
        float(row.get("risk_intensity_score") or 0)
        for row in rows
        if not row.get("crisis_keywords") and _within(row.get("created_at"), cutoff)
    ]
    average = round(sum(scores) / len(scores), 2) if scores else 0.0
    return {"count": len(scores), "average": average}


async def _crisis_flag(db, user_id: str, cutoff: datetime) -> bool:
    try:
        from services.users import get_by_identifier

        user = await get_by_identifier(db, user_id)
    except Exception:
        logger.exception("Consultation could not read user=%s", user_id)
        return False
    if not isinstance(user, dict) or not user.get("crisis_flag"):
        return False
    return _within(user.get("crisis_flagged_at"), cutoff)


async def collect_signals(db, user_id: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Everything the decision needs, with nothing recomputed."""
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.CONSULTATION_LOOKBACK_DAYS)
    threshold = settings.CONSULTATION_REPORT_METRIC_THRESHOLD

    reports = [r for r in await _reports(db, user_id, limit=5) if _within(r.get("created_at"), cutoff)]
    recent_three = reports[:3]
    metrics = [
        int(r.get("psychiatric_metric"))
        for r in recent_three
        if isinstance(r.get("psychiatric_metric"), int)
    ]
    elevated_reports = sum(1 for m in metrics if m >= threshold)
    report_crisis = any(bool(r.get("crisis_signal")) for r in recent_three)

    risk = await _risk_turns(db, user_id, cutoff)
    return {
        "lookback_days": settings.CONSULTATION_LOOKBACK_DAYS,
        "reports_considered": len(recent_three),
        "report_metrics": metrics,
        "elevated_reports": elevated_reports,
        "report_crisis_signal": report_crisis,
        "persistent_distress": await _persistent_distress(db, user_id, cutoff),
        "risk_turns": risk["count"],
        "risk_average": risk["average"],
        "crisis_flag_recent": await _crisis_flag(db, user_id, cutoff),
    }

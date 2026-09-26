"""Care status for the system prompt. Internal wording only."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

EMPTY = (
    "No professional-care status on file. Do not raise referral unless the "
    "action card context says a card is attached."
)

_LINES = {
    "REFERRED": (
        "Professional care has been recommended for this person. "
        "If they bring up therapy, counselling, or a doctor, support it warmly. "
        "Do not push it, do not repeat it unprompted, and do not sound alarmed."
    ),
    "MONITORING": (
        "Care status: observing. No referral now. Do not mention this."
    ),
    "NOT_NEEDED": (
        "Care status: no indication of need. Do not mention this."
    ),
}


async def build_care_context(db, user_id: str) -> str:
    try:
        from consultation.evaluate import latest_evaluation

        latest = await latest_evaluation(db, user_id)
    except Exception:
        logger.exception("Care context failed user=%s", user_id)
        return EMPTY
    if not latest:
        return EMPTY
    return _LINES.get(str(latest.get("status")), EMPTY)

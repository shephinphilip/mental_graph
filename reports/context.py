"""Welcome context from stored reports. Transcripts are not copied in."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

EMPTY = (
    "No previous session report. This is the first conversation on file. "
    "Do not invent an earlier event."
)


def format_prior_reports(reports: List[Dict[str, Any]]) -> str:
    if not reports:
        return EMPTY
    blocks = [
        "PRIOR SESSION REPORTS. These are readings, not a transcript.",
        "Do not quote the person and do not retell what they already said.",
        "A settled event must not be mentioned again.",
        "If an open event is listed, the welcome may ask once, lightly, whether that situation is settled.",
        "If none are open, invite them without inventing an event.",
    ]
    open_count = 0
    for report in reports:
        summary = str(report.get("psychiatric_summary") or report.get("summary") or "").strip()
        metric = report.get("psychiatric_metric")
        lines = []
        if summary:
            lines.append(f"Summary: {summary}")
        if isinstance(metric, int):
            lines.append(f"Session load (1 settled, 10 heavy): {metric}")
        open_events = [
            str(event.get("label")).strip()
            for event in (report.get("events") or [])
            if not event.get("resolved") and event.get("label")
        ]
        if open_events:
            open_count += len(open_events)
            lines.append("Open events:")
            lines.extend(f"- {label}" for label in open_events)
        else:
            lines.append("Open events: none.")
        if lines:
            blocks.append("\n".join(lines))
    if open_count == 0:
        blocks.append("No open events. Do not ask about the previous conversation.")
    return "\n\n".join(blocks)


async def build_prior_session_context(db, user_id: str) -> str:
    try:
        from reports.store import recent_reports

        reports = await recent_reports(db, user_id, limit=3)
    except Exception:
        logger.exception("Prior report context failed for user=%s", user_id)
        return EMPTY
    return format_prior_reports(reports)

"""Risk lists. Bands come from stored levels, risk turns, and active patterns."""

from __future__ import annotations

from typing import Optional

from dashboard.services.data import get_prepared
from dashboard.services.paging import paginate


async def summary(db, actor, scope) -> dict:
    prepared = await get_prepared(db, actor, scope)
    counts = {"total_students": len(prepared.rows), "critical": 0, "at_risk": 0, "watch": 0, "none": 0}
    for row in prepared.rows:
        severity = row.get("severity")
        if severity in {"critical", "at_risk", "watch"}:
            counts[severity] += 1
        else:
            counts["none"] += 1
    return counts


async def students(db, actor, scope, *, severity: Optional[str], page: int, limit: int, cursor):
    prepared = await get_prepared(db, actor, scope)
    rows = []
    for row in prepared.rows:
        if severity and row.get("severity") != severity:
            continue
        if not severity and not row.get("severity"):
            continue
        if severity is None and not row.get("severity"):
            continue
        rows.append(
            {
                "student_id": row["student_id"],
                "name": row["name"],
                "severity": row["severity"],
                "concern": row["concern"],
                "last_event_at": row["last_event_at"],
                "grade_id": row["grade_id"],
                "class_id": row["class_id"],
            }
        )
    if severity:
        rows = [row for row in rows if row["severity"] == severity]
    chunk, meta = paginate(rows, limit=limit, page=page, cursor=cursor, key=lambda row: row["student_id"])
    return chunk, meta

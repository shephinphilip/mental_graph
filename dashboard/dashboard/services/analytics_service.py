"""School analytics. Comparative benchmarks stay empty until a source exists."""

from __future__ import annotations

from dashboard.services.compute import (
    comparative_payload,
    grade_trends,
    wellbeing_payload,
    _subject_table,
)
from dashboard.services.data import get_prepared


async def grade_trend_payload(db, actor, scope):
    prepared = await get_prepared(db, actor, scope)
    return {"grades": grade_trends(prepared)}


async def subject_performance(db, actor, scope):
    prepared = await get_prepared(db, actor, scope)
    rows = prepared.rows
    if scope.subject_id:
        rows = [
            row
            for row in rows
            if any(item["subject_id"] == scope.subject_id for item in (row.get("subjects") or {}).values())
        ]
        # Recompute the table from the filtered subject only.
        narrowed = []
        for row in rows:
            subjects = {
                name: item
                for name, item in (row.get("subjects") or {}).items()
                if item["subject_id"] == scope.subject_id
            }
            narrowed.append({**row, "subjects": subjects})
        rows = narrowed
    return {"subjects": _subject_table(rows)}


async def wellbeing(db, actor, scope):
    prepared = await get_prepared(db, actor, scope)
    return wellbeing_payload(prepared)


async def comparative(db, actor, scope):
    prepared = await get_prepared(db, actor, scope)
    return comparative_payload(actor, prepared)

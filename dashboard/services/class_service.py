"""Class, quadrant, and subject views."""

from __future__ import annotations

from fastapi import HTTPException

from dashboard.identity import clean_token
from dashboard.services.compute import class_overview_payload, quadrant_payload, subject_overview_payload
from dashboard.services.data import get_prepared
from dashboard.services.paging import paginate


def _class_id(class_id: str) -> str:
    try:
        return clean_token(class_id, label="class_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def class_overview(db, actor, class_id: str, scope):
    class_id = _class_id(class_id)
    prepared = await get_prepared(db, actor, scope)
    payload = class_overview_payload(class_id, prepared)
    if not payload:
        raise HTTPException(status_code=404, detail="Class not found")
    return payload


async def performance_quadrant(db, actor, class_id: str, scope):
    class_id = _class_id(class_id)
    prepared = await get_prepared(db, actor, scope)
    if not any(row["class_id"] == class_id for row in prepared.rows):
        raise HTTPException(status_code=404, detail="Class not found")
    return quadrant_payload(class_id, prepared)


async def class_students(db, actor, class_id: str, scope, *, page: int, limit: int, cursor):
    class_id = _class_id(class_id)
    prepared = await get_prepared(db, actor, scope)
    rows = [row for row in prepared.rows if row["class_id"] == class_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Class not found")
    chunk, meta = paginate(rows, limit=limit, page=page, cursor=cursor, key=lambda row: row["student_id"])
    return [
        {
            "student_id": row["student_id"],
            "name": row["name"],
            "grade_id": row["grade_id"],
            "latest_percentage": row["latest"],
            "severity": row["severity"],
            "quadrant": row["quadrant"],
        }
        for row in chunk
    ], meta


async def subject_overview(db, actor, class_id: str, subject_id: str, scope):
    class_id = _class_id(class_id)
    try:
        subject_id = clean_token(subject_id, label="subject_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    prepared = await get_prepared(db, actor, scope)
    payload = subject_overview_payload(class_id, subject_id, prepared)
    if payload is None:
        if not any(row["class_id"] == class_id for row in prepared.rows):
            raise HTTPException(status_code=404, detail="Class not found")
        raise HTTPException(status_code=404, detail="Subject not found")
    return payload

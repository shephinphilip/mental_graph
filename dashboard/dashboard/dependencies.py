"""Resolve the signed-in school staff member. School id is never taken from the client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend_core.deps import authenticated_user_id
from dashboard.identity import clean_token, clean_year, school_id_of, school_name_of, tenant_key
from dashboard.metrics import Scope
from dashboard.permissions import can_access_dashboard, is_active, primary_role, role_set
from dashboard.services.paging import decode_cursor
from database import get_db
from backend_core.users import get_by_identifier


@dataclass
class DashboardActor:
    user_id: str
    name: str
    email: Optional[str]
    roles: frozenset
    primary_role: str
    school_key: str
    school_id: str
    school_name: str
    board: Optional[str]
    query_user: dict


async def resolve_actor(db, user_id: str) -> DashboardActor:
    user = await get_by_identifier(db, user_id)
    if not isinstance(user, dict):
        raise HTTPException(status_code=401, detail="Account is not available")
    if not is_active(user):
        raise HTTPException(status_code=403, detail="Account is not active")
    roles = role_set(user)
    if not can_access_dashboard(roles):
        raise HTTPException(status_code=403, detail="This account cannot open the school dashboard")
    key = tenant_key(user)
    if not key:
        raise HTTPException(status_code=403, detail="No school is assigned to this account")
    school_id = school_id_of(user)
    school_name = school_name_of(user)
    return DashboardActor(
        user_id=str(user.get("user_id") or user_id),
        name=user.get("name") or "",
        email=user.get("email"),
        roles=roles,
        primary_role=primary_role(roles),
        school_key=key,
        school_id=school_id,
        school_name=school_name,
        board=user.get("board") or user.get("school_board"),
        query_user={"school_id": school_id, "school": school_name, "user_id": str(user.get("user_id") or user_id)},
    )


async def dashboard_actor(
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> DashboardActor:
    return await resolve_actor(db, user_id)


def dashboard_scope(
    academic_year: Optional[str] = None,
    grade_id: Optional[str] = None,
    class_id: Optional[str] = None,
    subject_id: Optional[str] = None,
    from_at: Optional[datetime] = Query(default=None, alias="from"),
    to_at: Optional[datetime] = Query(default=None, alias="to"),
) -> Scope:
    try:
        year = clean_year(academic_year)
        if grade_id:
            grade_id = clean_token(grade_id, label="grade_id")
        if class_id:
            class_id = clean_token(class_id, label="class_id")
        if subject_id:
            subject_id = clean_token(subject_id, label="subject_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if from_at and to_at and from_at > to_at:
        raise HTTPException(status_code=400, detail="from must be before to")
    return Scope(
        academic_year=year,
        grade_id=grade_id,
        class_id=class_id,
        subject_id=subject_id,
        from_at=from_at,
        to_at=to_at,
    )


def page_query(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: Optional[str] = None,
) -> tuple:
    decoded = None
    if cursor:
        try:
            decoded = decode_cursor(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return page, limit, decoded

"""Asynchronous school reports. The HTTP request only stores the job."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from config.config import logger
from dashboard.events.publisher import publish
from dashboard.identity import clean_token, clean_year
from dashboard.metrics import Scope
from dashboard.repositories.dashboard_repository import load_bundle
from dashboard.repositories.report_repository import create_report, get_report, list_reports, save_report
from dashboard.services.compute import (
    comparative_payload,
    grade_trends,
    overview_payload,
    prepare,
    wellbeing_payload,
)
from dashboard.services.notification_service import create_notification
from dashboard.services.paging import paginate
from dashboard.services.settings_service import get_settings
from dashboard.services.student_service import require_student


def _public(doc: dict, *, include_body: bool) -> dict[str, Any]:
    payload = {
        "report_id": doc.get("report_id"),
        "type": (doc.get("spec") or {}).get("type"),
        "scope": (doc.get("spec") or {}).get("scope"),
        "status": doc.get("status"),
        "created_at": doc.get("created_at"),
        "ready_at": doc.get("ready_at"),
        "error": doc.get("error"),
        "charts_available": False,
        "charts_reason": "No chart renderer is installed. include_charts is stored and not drawn.",
    }
    if include_body:
        payload["body"] = doc.get("body")
    return payload


async def enqueue(db, actor, spec: dict) -> dict:
    spec = dict(spec)
    try:
        if spec.get("academic_year"):
            spec["academic_year"] = clean_year(spec["academic_year"])
        for field in ("grade_id", "class_id", "student_id"):
            if spec.get(field):
                spec[field] = clean_token(spec[field], label=field)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    scope_name = spec.get("scope") or "school"
    if scope_name == "grade" and not spec.get("grade_id"):
        raise HTTPException(status_code=400, detail="grade_id is required when scope is grade")
    if scope_name == "class" and not spec.get("class_id"):
        raise HTTPException(status_code=400, detail="class_id is required when scope is class")
    if scope_name == "student":
        if not spec.get("student_id"):
            raise HTTPException(status_code=400, detail="student_id is required when scope is student")
        await require_student(db, actor, spec["student_id"])
    if scope_name == "class":
        from dashboard.metrics import Scope
        from dashboard.services.data import get_prepared

        prepared = await get_prepared(db, actor, Scope())
        if not any(row["class_id"] == spec["class_id"] for row in prepared.rows):
            raise HTTPException(status_code=404, detail="Class not found")
    doc = await create_report(
        db,
        school_key=actor.school_key,
        owner_user_id=actor.user_id,
        spec=spec,
    )
    await save_report(
        db,
        actor.school_key,
        doc["report_id"],
        {
            "tenant": {
                "school_id": actor.school_id,
                "school": actor.school_name,
                "user_id": actor.user_id,
                "board": actor.board,
                "name": actor.name,
                "roles": sorted(actor.roles),
                "primary_role": actor.primary_role,
                "email": actor.email,
            }
        },
    )
    return _public(doc, include_body=False)


async def generate(db, school_key: str, report_id: str) -> None:
    doc = await get_report(db, school_key, report_id)
    if not doc or doc.get("status") in {"ready", "failed"}:
        return
    await save_report(db, school_key, report_id, {"status": "generating"})
    try:
        tenant = doc.get("tenant") or {}
        spec = doc.get("spec") or {}
        scope_name = spec.get("scope") or "school"
        scope = Scope(
            academic_year=spec.get("academic_year"),
            grade_id=spec.get("grade_id") if scope_name in {"grade", "class", "student"} else None,
            class_id=spec.get("class_id") if scope_name in {"class", "student"} else None,
        )
        bundle = await load_bundle(
            db,
            {"school_id": tenant.get("school_id"), "school": tenant.get("school")},
            school_key,
        )
        prepared = prepare(bundle, scope)
        actor = _actor_from_tenant(tenant, school_key)
        body = _body(spec, actor, prepared)
        from datetime import datetime, timezone

        ready_at = datetime.now(timezone.utc)
        await save_report(
            db,
            school_key,
            report_id,
            {"status": "ready", "body": body, "ready_at": ready_at, "error": None},
        )
        publish(school_key, "report_ready", report_id=report_id, report_type=spec.get("type"), status="ready")
        settings = await get_settings(db, actor)
        if settings.get("report_generation_alerts", True):
            await create_notification(
                db,
                actor=actor,
                category="report_ready",
                title="Report ready",
                body=f"{spec.get('type')} report is ready.",
                recipient_user_id=actor.user_id,
                resource_type="report",
                resource_id=report_id,
            )
    except Exception:
        logger.exception("dashboard report failed report_id=%s", report_id)
        await save_report(
            db,
            school_key,
            report_id,
            {"status": "failed", "error": "Report generation failed."},
        )


class _Actor:
    def __init__(self, tenant: dict, school_key: str) -> None:
        self.user_id = tenant.get("user_id")
        self.name = tenant.get("name") or ""
        self.email = tenant.get("email")
        self.roles = frozenset(tenant.get("roles") or [])
        self.primary_role = tenant.get("primary_role") or ""
        self.school_key = school_key
        self.school_id = tenant.get("school_id") or ""
        self.school_name = tenant.get("school") or ""
        self.board = tenant.get("board")
        self.query_user = {"school_id": self.school_id, "school": self.school_name}


def _actor_from_tenant(tenant: dict, school_key: str) -> _Actor:
    return _Actor(tenant, school_key)


def _strip_names(overview: dict) -> dict:
    payload = dict(overview)
    payload["top_performers"] = [
        {"latest_percentage": row.get("latest_percentage"), "class_id": row.get("class_id")}
        for row in overview.get("top_performers") or []
    ]
    payload["teachers"] = [
        {
            "subjects": row.get("subjects"),
            "classes": row.get("classes"),
            "rating_available": False,
            "performance": None,
        }
        for row in overview.get("teachers") or []
    ]
    return payload


def _body(spec: dict, actor, prepared) -> dict:
    report_type = spec.get("type")
    overview = overview_payload(actor, prepared)
    sections: dict[str, Any] = {}
    if report_type == "board":
        sections["overview"] = _strip_names(overview)
        sections["grade_trends"] = grade_trends(prepared)
    elif report_type == "wellbeing":
        sections["wellbeing"] = wellbeing_payload(prepared)
        sections["risk"] = overview["risk"]
    else:
        sections["overview"] = _strip_names(overview)
        student_id = spec.get("student_id")
        if student_id:
            match = next((row for row in prepared.rows if row["student_id"] == student_id), None)
            if match:
                sections["student"] = {
                    "student_id": match["student_id"],
                    "name": match["name"],
                    "latest_percentage": match["latest"],
                    "growth_delta": match["delta"],
                    "attendance": match["attendance"],
                    "risk_severity": match["severity"],
                    "quadrant": match["quadrant"],
                }
    insight = None
    if spec.get("include_ai_insights"):
        insight = overview["insight"]
    return {
        "type": report_type,
        "scope": spec.get("scope"),
        "summary": {
            "student_count": overview["counts"]["students"],
            "academic_index": overview["academic_health"]["index"],
            "risk": overview["risk"],
        },
        "sections": sections,
        "insight": insight,
        "llm_used": False,
        "charts": None,
    }


async def fetch(db, actor, report_id: str) -> dict:
    doc = await get_report(db, actor.school_key, report_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Report not found")
    return doc


async def list_jobs(db, actor, *, page: int, limit: int, cursor):
    rows = await list_reports(db, actor.school_key, 200)
    for row in rows:
        row.pop("body", None)
    chunk, meta = paginate(
        rows,
        limit=limit,
        page=page,
        cursor=cursor,
        reverse=True,
        key=lambda row: f"{row.get('created_at')}|{row.get('report_id')}",
    )
    return [_public(row, include_body=False) for row in chunk], meta


async def preview(db, actor, report_id: str) -> dict:
    doc = await fetch(db, actor, report_id)
    if doc.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Report is not ready")
    body = doc.get("body") or {}
    return {
        "report_id": report_id,
        "status": "ready",
        "summary": body.get("summary"),
        "insight": body.get("insight"),
        "charts_available": False,
    }


async def download(db, actor, report_id: str) -> dict:
    doc = await fetch(db, actor, report_id)
    if doc.get("status") != "ready":
        raise HTTPException(status_code=409, detail="Report is not ready")
    from dashboard.services.audit import audit

    await audit(
        db,
        actor=actor,
        action="report.download",
        resource_type="report",
        resource_id=report_id,
    )
    return _public(doc, include_body=True)

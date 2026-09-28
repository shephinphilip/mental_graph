"""School dashboard HTTP routes. Mounted only at /api/v1/dashboard."""

from __future__ import annotations

import asyncio
import json
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from dashboard.dependencies import DashboardActor, dashboard_actor, dashboard_scope, page_query
from dashboard.events import bus
from dashboard.http import ok
from dashboard.identity import clean_token, clean_year
from dashboard.metrics import Scope
from dashboard.schemas import (
    AssistantRequest,
    InterventionCreate,
    InterventionPatch,
    NotifyCounselorRequest,
    ParentContactRequest,
    ReportCreate,
    SettingsPatch,
    SupportPlanRequest,
    TeacherMessageRequest,
    TeacherReviewRequest,
)
from dashboard.services import analytics_service, assistant_service, class_service, context_service
from dashboard.services import intervention_service, notification_service, overview_service, report_service
from dashboard.services import risk_service, settings_service, student_service, teacher_service
from dashboard.services.student_service import require_student
from database import get_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/context")
async def dashboard_context(actor: DashboardActor = Depends(dashboard_actor), db: AsyncIOMotorDatabase = Depends(get_db)):
    return ok(await context_service.context_payload(db, actor))


@router.get("/academic-years")
async def dashboard_years(actor: DashboardActor = Depends(dashboard_actor), db: AsyncIOMotorDatabase = Depends(get_db)):
    return ok(await context_service.academic_years(db, actor))


@router.get("/grades")
async def dashboard_grades(actor: DashboardActor = Depends(dashboard_actor), db: AsyncIOMotorDatabase = Depends(get_db)):
    return ok(await context_service.grades(db, actor))


@router.get("/classes")
async def dashboard_classes(
    grade_id: Optional[str] = None,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    if grade_id:
        try:
            grade_id = clean_token(grade_id, label="grade_id")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(await context_service.classes(db, actor, grade_id=grade_id))


@router.get("/subjects")
async def dashboard_subjects(
    grade_id: Optional[str] = None,
    class_id: Optional[str] = None,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        if grade_id:
            grade_id = clean_token(grade_id, label="grade_id")
        if class_id:
            class_id = clean_token(class_id, label="class_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(await context_service.subjects(db, actor, grade_id=grade_id, class_id=class_id))


@router.get("/overview")
async def dashboard_overview(
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await overview_service.overview(db, actor, scope))


@router.get("/classes/{class_id}/overview")
async def class_overview(
    class_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await class_service.class_overview(db, actor, class_id, scope))


@router.get("/classes/{class_id}/performance-quadrant")
async def class_quadrant(
    class_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await class_service.performance_quadrant(db, actor, class_id, scope))


@router.get("/classes/{class_id}/students")
async def class_students(
    class_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    page: tuple = Depends(page_query),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    rows, meta = await class_service.class_students(
        db, actor, class_id, scope, page=page[0], limit=page[1], cursor=page[2]
    )
    return ok(rows, **meta)


@router.get("/classes/{class_id}/subjects/{subject_id}/overview")
async def subject_overview(
    class_id: str,
    subject_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await class_service.subject_overview(db, actor, class_id, subject_id, scope))


@router.get("/students/{student_id}/profile")
async def student_profile(
    student_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await student_service.profile(db, actor, student_id))


@router.get("/students/{student_id}/interventions")
async def student_interventions(
    student_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    page: tuple = Depends(page_query),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    rows, meta = await student_service.interventions(
        db, actor, student_id, page=page[0], limit=page[1], cursor=page[2]
    )
    return ok(rows, **meta)


@router.post("/students/{student_id}/parent-contact")
async def parent_contact(
    student_id: str,
    payload: ParentContactRequest,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(
        await student_service.record_parent_contact(
            db, actor, student_id, payload.message, payload.reason
        )
    )


@router.post("/students/{student_id}/notify-counselor")
async def notify_counselor(
    student_id: str,
    payload: NotifyCounselorRequest,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await student_service.notify_counselor(db, actor, student_id, payload.message))


@router.get("/risk/summary")
async def risk_summary(
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await risk_service.summary(db, actor, scope))


@router.get("/risk/students")
async def risk_students(
    severity: Optional[Literal["critical", "at_risk", "watch"]] = None,
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    page: tuple = Depends(page_query),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    rows, meta = await risk_service.students(
        db,
        actor,
        scope,
        severity=severity,
        page=page[0],
        limit=page[1],
        cursor=page[2],
    )
    return ok(rows, **meta)


@router.post("/interventions", status_code=201)
async def create_intervention(
    payload: InterventionCreate,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    student = await require_student(db, actor, payload.student_id)
    return ok(
        await intervention_service.create_intervention(
            db,
            actor,
            student_id=student["user_id"],
            title=payload.title,
            plan=payload.plan,
            status=payload.status,
            assignee_user_id=payload.assignee_user_id,
        )
    )


@router.get("/interventions/{intervention_id}")
async def get_intervention(
    intervention_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        intervention_id = clean_token(intervention_id, label="intervention_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(await intervention_service.get_intervention(db, actor, intervention_id))


@router.patch("/interventions/{intervention_id}")
async def patch_intervention(
    intervention_id: str,
    payload: InterventionPatch,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        intervention_id = clean_token(intervention_id, label="intervention_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(
        await intervention_service.update_intervention(
            db, actor, intervention_id, payload.model_dump(exclude_unset=True)
        )
    )


@router.get("/teachers")
async def teachers(
    subject_id: Optional[str] = None,
    class_id: Optional[str] = None,
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    page: tuple = Depends(page_query),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        if subject_id:
            subject_id = clean_token(subject_id, label="subject_id")
        if class_id:
            class_id = clean_token(class_id, label="class_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rows, meta = await teacher_service.list_teachers(
        db,
        actor,
        scope,
        subject_id=subject_id,
        class_id=class_id,
        page=page[0],
        limit=page[1],
        cursor=page[2],
    )
    return ok(rows, **meta)


@router.get("/teachers/{teacher_id}")
async def teacher_profile(
    teacher_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await teacher_service.teacher_profile(db, actor, teacher_id))


@router.post("/teachers/{teacher_id}/messages", status_code=201)
async def teacher_message(
    teacher_id: str,
    payload: TeacherMessageRequest,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await teacher_service.send_message(db, actor, teacher_id, payload.message))


@router.post("/teachers/{teacher_id}/reviews", status_code=201)
async def teacher_review(
    teacher_id: str,
    payload: TeacherReviewRequest,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(
        await teacher_service.add_review(db, actor, teacher_id, payload.notes, payload.focus_areas)
    )


@router.post("/teachers/{teacher_id}/support-plan", status_code=201)
async def teacher_support(
    teacher_id: str,
    payload: SupportPlanRequest,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(
        await teacher_service.add_support_plan(
            db, actor, teacher_id, payload.summary, payload.focus_areas
        )
    )


@router.get("/analytics/grade-trends")
async def grade_trends(
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await analytics_service.grade_trend_payload(db, actor, scope))


@router.get("/analytics/subject-performance")
async def subject_performance(
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await analytics_service.subject_performance(db, actor, scope))


@router.get("/analytics/wellbeing")
async def wellbeing(
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await analytics_service.wellbeing(db, actor, scope))


@router.get("/analytics/comparative")
async def comparative(
    actor: DashboardActor = Depends(dashboard_actor),
    scope: Scope = Depends(dashboard_scope),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await analytics_service.comparative(db, actor, scope))


@router.get("/notifications")
async def notifications(
    unread_only: bool = False,
    actor: DashboardActor = Depends(dashboard_actor),
    page: tuple = Depends(page_query),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    rows, meta = await notification_service.list_notifications(
        db,
        actor,
        unread_only=unread_only,
        page=page[0],
        limit=page[1],
        cursor=page[2],
    )
    return ok(rows, **meta)


@router.post("/notifications/read-all")
async def notifications_read_all(
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await notification_service.mark_all_read(db, actor))


@router.post("/notifications/{notification_id}/read")
async def notification_read(
    notification_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        notification_id = clean_token(notification_id, label="notification_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(await notification_service.mark_read(db, actor, notification_id))


@router.get("/events")
async def events(request: Request, actor: DashboardActor = Depends(dashboard_actor)):
    queue = bus.subscribe(actor.school_key)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                name = str(event.get("event") or "message")
                yield f"event: {name}\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            bus.unsubscribe(actor.school_key, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/reports", status_code=202)
async def create_report(
    payload: ReportCreate,
    background: BackgroundTasks,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    job = await report_service.enqueue(db, actor, payload.model_dump())
    background.add_task(report_service.generate, db, actor.school_key, job["report_id"])
    return JSONResponse(status_code=202, content=ok(job))


@router.get("/reports")
async def list_reports(
    actor: DashboardActor = Depends(dashboard_actor),
    page: tuple = Depends(page_query),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    rows, meta = await report_service.list_jobs(
        db, actor, page=page[0], limit=page[1], cursor=page[2]
    )
    return ok(rows, **meta)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        report_id = clean_token(report_id, label="report_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = await report_service.fetch(db, actor, report_id)
    return ok(report_service._public(doc, include_body=False))


@router.get("/reports/{report_id}/preview")
async def preview_report(
    report_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        report_id = clean_token(report_id, label="report_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(await report_service.preview(db, actor, report_id))


@router.get("/reports/{report_id}/download")
async def download_report(
    report_id: str,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    try:
        report_id = clean_token(report_id, label="report_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = await report_service.download(db, actor, report_id)
    raw = json.dumps(ok(payload)).encode("utf-8")
    return Response(
        content=raw,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="zenark-{report_id}.json"'},
    )


@router.post("/assistant")
async def assistant(
    payload: AssistantRequest,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    context = payload.context
    try:
        scope = Scope(
            academic_year=clean_year(context.academic_year),
            grade_id=clean_token(context.grade_id, label="grade_id") if context.grade_id else None,
            class_id=clean_token(context.class_id, label="class_id") if context.class_id else None,
            subject_id=clean_token(context.subject_id, label="subject_id") if context.subject_id else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(await assistant_service.answer(db, actor, payload.message, scope))


@router.get("/settings")
async def get_settings(
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    return ok(await settings_service.get_settings(db, actor))


@router.patch("/settings")
async def patch_settings(
    payload: SettingsPatch,
    actor: DashboardActor = Depends(dashboard_actor),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    patch = payload.model_dump(exclude_unset=True)
    return ok(await settings_service.update_settings(db, actor, patch))

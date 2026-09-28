"""Indexes for dashboard queries and the collections this module owns."""

from __future__ import annotations

from config.config import logger


async def ensure_dashboard_indexes(db) -> None:
    await db["users"].create_index(
        [("school_id", 1), ("isActive", 1)],
        name="users_school_id_active",
    )
    await db["users"].create_index(
        [("school", 1), ("isActive", 1)],
        name="users_school_name_active",
    )
    await db["marks"].create_index(
        [("student_id", 1), ("exam_date", -1)],
        name="marks_student_exam_date",
    )
    await db["marks"].create_index(
        [("student_id", 1), ("subject", 1), ("exam_date", -1)],
        name="marks_student_subject_date",
    )
    await db["dashboard_interventions"].create_index(
        [("school_key", 1), ("intervention_id", 1)],
        unique=True,
        name="dashboard_intervention_school_id",
    )
    await db["dashboard_interventions"].create_index(
        [("school_key", 1), ("student_id", 1), ("updated_at", -1)],
        name="dashboard_intervention_student",
    )
    await db["dashboard_notifications"].create_index(
        [("school_key", 1), ("recipient_user_id", 1), ("read", 1), ("created_at", -1)],
        name="dashboard_notification_inbox",
    )
    await db["dashboard_reports"].create_index(
        [("school_key", 1), ("report_id", 1)],
        unique=True,
        name="dashboard_report_school_id",
    )
    await db["dashboard_reports"].create_index(
        [("school_key", 1), ("created_at", -1)],
        name="dashboard_report_recent",
    )
    await db["dashboard_settings"].create_index(
        [("school_key", 1), ("user_id", 1)],
        unique=True,
        name="dashboard_settings_user",
    )
    await db["dashboard_audit"].create_index(
        [("school_key", 1), ("created_at", -1)],
        name="dashboard_audit_recent",
    )
    await db["dashboard_teacher_actions"].create_index(
        [("school_key", 1), ("teacher_id", 1), ("created_at", -1)],
        name="dashboard_teacher_actions_recent",
    )
    logger.info("Dashboard indexes ensured")

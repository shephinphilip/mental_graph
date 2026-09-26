"""Startup index orchestration. Domain modules still declare their own indexes."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def ensure_all_indexes(db) -> None:
    from services.graph_rag import ensure_graph_constraints
    from services.apm import ensure_apm_indexes
    from services.chat_history import ensure_message_indexes
    from services.patterns import ensure_pattern_indexes
    from services.meditation.service import ensure_meditation_indexes
    from sleep.indexes import ensure_sleep_indexes
    from journaling.indexes import ensure_journal_indexes
    from tasks.indexes import ensure_task_indexes
    from reports.indexes import ensure_report_indexes
    from consultation.indexes import ensure_consultation_indexes
    from student_memory.indexes import ensure_student_memory_indexes
    from tracking.indexes import ensure_tracking_indexes

    await ensure_graph_constraints(db)
    await ensure_apm_indexes(db)
    await ensure_message_indexes(db)
    await ensure_pattern_indexes(db)
    await ensure_meditation_indexes(db)
    await ensure_sleep_indexes(db)
    await ensure_journal_indexes(db)
    await ensure_task_indexes(db)
    await ensure_report_indexes(db)
    await ensure_consultation_indexes(db)
    await ensure_student_memory_indexes(db)
    await ensure_tracking_indexes(db)
    await db["users"].create_index("email", unique=True)
    await db["users"].create_index("user_id", unique=True)
    logger.info("All domain indexes ensured")

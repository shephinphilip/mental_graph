"""Idempotent user erasure. Job documents store counts and status, never content."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from config.config import logger

JOBS = "erasure_jobs"

# Each entry is (collection, field that must equal this user).
_OWNED = (
    ("messages", "user_id"),
    ("journal_entries", "user_id"),
    ("sleep_logs", "user_id"),
    ("mood_logs", "user_id"),
    ("daily_tasks", "user_id"),
    ("habit_events", "user_id"),
    ("meditation_executions", "user_id"),
    ("meditation_offers", "user_id"),
    ("student_psychological_profiles", "user_id"),
    ("student_memories", "user_id"),
    ("user_patterns", "user_id"),
    ("pattern_evidence", "user_id"),
    ("user_insights", "user_id"),
    ("session_reports", "user_id"),
    ("apm_nodes", "user_id"),
    ("apm_edges", "user_id"),
    ("apm_events", "user_id"),
    ("graph_nodes", "user_id"),
    ("graph_relationships", "user_id"),
    ("proactive_questions", "user_id"),
    ("exam_buddy_nodes", "user_id"),
    ("exam_buddy_relationships", "user_id"),
    ("gds_snapshots", "user_id"),
    ("user_risk_turns", "user_id"),
)


async def _delete_owned(db, user_id: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for collection, field in _OWNED:
        result = await db[collection].delete_many({field: user_id})
        counts[collection] = int(getattr(result, "deleted_count", 0) or 0)
    await db["escalation_cases"].update_many(
        {"user_id": user_id},
        {"$unset": {"narrative": "", "body": "", "transcript": ""}},
    )
    from student_memory.store import clear_derived_user_memory

    await clear_derived_user_memory(db, user_id)
    return counts


async def start_erasure(db, user_id: str) -> Dict[str, Any]:
    existing = await db[JOBS].find_one(
        {"user_id": user_id, "status": {"$in": ["queued", "running"]}}
    )
    if existing:
        return _public(existing)
    prior = await db[JOBS].find_one({"user_id": user_id, "status": "succeeded"})
    if prior:
        return _public(prior)
    job_id = f"erase_{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "running",
        "counts": {},
        "errors": [],
        "created_at": datetime.now(timezone.utc),
    }
    await db[JOBS].insert_one(job)
    try:
        counts = await _delete_owned(db, user_id)
        job["counts"] = counts
        job["status"] = "succeeded"
    except Exception as exc:
        logger.exception("Erasure failed user=%s", user_id)
        job["status"] = "failed"
        job["errors"] = [type(exc).__name__]
    await db[JOBS].update_one({"job_id": job_id}, {"$set": _public(job)})
    return _public(job)


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": job.get("job_id"),
        "user_id": job.get("user_id"),
        "status": job.get("status"),
        "counts": job.get("counts") or {},
        "errors": job.get("errors") or [],
    }


async def rerun(db, job_id: str) -> Dict[str, Any]:
    job = await db[JOBS].find_one({"job_id": job_id})
    if not job:
        raise ValueError("unknown_job")
    if job.get("status") == "succeeded":
        job["counts"] = {name: 0 for name, _field in _OWNED}
        return _public(job)
    counts = await _delete_owned(db, job["user_id"])
    job["counts"] = counts
    job["status"] = "succeeded"
    await db[JOBS].update_one({"job_id": job_id}, {"$set": _public(job)})
    return _public(job)


async def ensure_erasure_indexes(db) -> None:
    await db[JOBS].create_index("job_id", unique=True)
    await db[JOBS].create_index([("user_id", 1), ("status", 1)])

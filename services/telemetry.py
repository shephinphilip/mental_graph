"""Only these operational signals may be recorded. Unknown names fail closed."""

from __future__ import annotations

ALLOWED_METRICS = frozenset(
    {
        "request_id",
        "route",
        "status",
        "latency",
        "crisis_fast_track",
        "safety_class",
        "care_band",
        "delivery",
        "validator_reject_reason",
        "language",
        "language_source",
        "practice_started",
        "practice_completed",
        "practice_dismissed",
        "helpful",
        "not_helpful",
        "erasure_job_status",
        "provider_error",
        "provider_fallback",
        "proactive_decision",
        "proactive_trigger_type",
        "proactive_suppression",
        "proactive_status",
        "proactive_validation",
        "proactive_retry",
    }
)


def register_metric(name: str) -> str:
    if name not in ALLOWED_METRICS:
        raise ValueError(f"metric not allowlisted: {name}")
    return name

"""
Longitudinal User Pattern Detection Engine.

Separates facts → observations → patterns → hypotheses. Does not replace
Graph RAG or APM; injects a bounded, consent-gated pattern context string
into the companion prompt.
"""

from services.patterns.service import (
    ensure_pattern_indexes,
    get_pattern_context,
    record_pattern_feedback,
    run_pattern_detection,
)

__all__ = [
    "ensure_pattern_indexes",
    "get_pattern_context",
    "record_pattern_feedback",
    "run_pattern_detection",
]

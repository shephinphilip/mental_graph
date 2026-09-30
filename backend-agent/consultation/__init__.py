"""Decides whether recent conversations warrant professional care.

Reads the risk window, session reports, and the crisis flag that already
exist. It does not score turns itself.
"""

from consultation.context import build_care_context
from consultation.evaluate import (
    DECISIONS,
    evaluate_user_for_consultation,
    latest_evaluation,
    manual_override,
)

__all__ = [
    "DECISIONS",
    "build_care_context",
    "evaluate_user_for_consultation",
    "latest_evaluation",
    "manual_override",
]

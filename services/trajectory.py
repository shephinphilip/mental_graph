"""Trajectory labels from the existing capped risk window. Internal only."""

from __future__ import annotations

from typing import Any, Dict, List


def label_trajectory(turns: List[Dict[str, Any]], *, high: float = 8.0) -> str:
    scored = [
        float(row.get("risk_intensity_score") or 0)
        for row in turns
        if not row.get("crisis_keyword")
    ]
    if not scored:
        return "unknown"
    high_flags = [score >= high for score in scored]
    if high_flags[-1] and any(high_flags[:-1]):
        return "persistent"
    if not high_flags[-1] and any(high_flags):
        return "recovering"
    if high_flags[-1] and not any(high_flags[:-1]):
        return "isolated"
    return "unknown"

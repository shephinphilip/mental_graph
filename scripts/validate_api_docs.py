"""Compare registered FastAPI routes to docs/api/API_INVENTORY.md.

Does not start Mongo. Importing ``app`` is enough to read the router.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "api" / "API_INVENTORY.md"
FRONTEND_DOC = ROOT / "docs" / "api" / "FRONTEND_API_DOCUMENTATION.md"

SKIP = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def mounted_paths():
    sys.path.insert(0, str(ROOT))
    from app import app

    found = set()
    from fastapi.routing import APIWebSocketRoute

    for route in app.router.routes:
        if isinstance(route, APIWebSocketRoute):
            found.add(("WS", route.path))
            continue
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts:
            for ctx in contexts():
                path = ctx.path
                methods = set(ctx.methods or set())
                if not methods and path.endswith("/ws/psychiatrist-voice"):
                    found.add(("WS", path))
                    continue
                for method in methods - {"HEAD"}:
                    if method == "GET" and path.endswith("/ws/psychiatrist-voice"):
                        found.add(("WS", path))
                    else:
                        found.add((method, path))
            continue
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path and path not in SKIP:
            for method in methods - {"HEAD"}:
                found.add((method, path))
    return {(m, p) for m, p in found if p not in SKIP}


def inventory_rows():
    text = INVENTORY.read_text(encoding="utf-8")
    rows = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        method, path = cells[0], cells[1]
        if method in {"Method", "---"} or not path.startswith("/"):
            continue
        rows.append((method, path))
    return rows


def headings_in_frontend_doc():
    text = FRONTEND_DOC.read_text(encoding="utf-8")
    return set(re.findall(r"^## (GET|POST|PATCH|DELETE|WS) (/[^\s]+)", text, re.M))


def validate_sample_models() -> list[str]:
    """Confirm documented request examples parse as the real Pydantic models."""
    from datetime import datetime, timezone

    from schemas import (
        AcceptReportTaskRequest,
        ChatMessageRequest,
        ConsultationBatchRequest,
        ConsultationManualRequest,
        ConsultationOverrideRequest,
        HabitCheckInRequest,
        HabitCreateRequest,
        HabitPatchRequest,
        JournalEntryRequest,
        LanguagePreferenceRequest,
        LoginRequest,
        MeditationCompleteRequest,
        MeditationFeedbackRequest,
        MeditationPreviewRequest,
        MeditationStartRequest,
        MoodLogRequest,
        PatternFeedbackRequest,
        SessionReportRequest,
        SignupRequest,
        SleepLogRequest,
        StreakVisibilityRequest,
        TaskCompleteRequest,
        TaskCustomRequest,
        WelcomeRequest,
    )

    errors = []
    samples = [
        (LoginRequest, {"email": "user@example.com", "password": "ExamplePassword"}),
        (SignupRequest, {"email": "user@example.com", "password": "ExamplePassword", "name": "Ananya"}),
        (WelcomeRequest, {"user_id": "usr_ab12cd34", "session_id": "session_123"}),
        (ChatMessageRequest, {"user_id": "usr_ab12cd34", "session_id": "session_123", "message": "I've been feeling overwhelmed today."}),
        (SessionReportRequest, {"session_id": "session_123"}),
        (AcceptReportTaskRequest, {"session_id": "session_123", "task_id": "proposal_ab12cd34"}),
        (SleepLogRequest, {"bedtime": "22:30", "wake_up_time": "06:30", "date": "2026-09-26", "total_duration_minutes": 480}),
        (JournalEntryRequest, {"title": "Evening note", "content": "The mock felt heavier than I expected.", "mood": "anxious"}),
        (TaskCompleteRequest, {"task_id": "task_1a2b3c4d"}),
        (TaskCustomRequest, {"title": "Walk after dinner", "description": "Ten minutes"}),
        (MeditationStartRequest, {"meditation_id": "med_abc", "execution_nonce": "nonce_from_card"}),
        (MeditationCompleteRequest, {"execution_id": "mexe_ab12cd34ef56", "execution_nonce": "nonce_from_card"}),
        (MeditationFeedbackRequest, {"execution_id": "mexe_ab12cd34ef56", "execution_nonce": "nonce_from_card", "feedback": "HELPFUL"}),
        (MeditationPreviewRequest, {"message": "I cannot sleep"}),
        (LanguagePreferenceRequest, {"language": "HINDI"}),
        (PatternFeedbackRequest, {"pattern_id": "pat_ab12cd34ef56", "event_type": "CONFIRM"}),
        (ConsultationManualRequest, {}),
        (ConsultationOverrideRequest, {"user_id": "usr_ab12cd34", "status": "MONITORING", "reason": "Reviewed in clinic today"}),
        (ConsultationBatchRequest, {"user_ids": ["usr_ab12cd34"]}),
        (MoodLogRequest, {"mood": "anxious", "score": 3}),
        (HabitCreateRequest, {"title": "Morning walk"}),
        (HabitPatchRequest, {"status": "paused"}),
        (HabitCheckInRequest, {}),
        (StreakVisibilityRequest, {"show_streaks": True}),
    ]
    for model, payload in samples:
        try:
            model.model_validate(payload)
        except Exception as exc:
            errors.append(f"{model.__name__}: {exc}")
    MoodLogRequest.model_validate(
        {"mood": "anxious", "logged_at": datetime(2026, 9, 26, tzinfo=timezone.utc)}
    )
    return errors


def main() -> int:
    routes = mounted_paths()
    inventory = set(inventory_rows())
    missing_from_inventory = sorted(routes - inventory)
    extra_in_inventory = sorted(inventory - routes)
    doc_heads = headings_in_frontend_doc()
    v1 = {(m, p) for m, p in routes if p.startswith("/api/v1") or p in {"/api/v1/health", "/api/v1/health/live", "/api/v1/health/ready"}}
    # Every unique operation has a v1 twin except nothing — health and all domains are remounted.
    missing_from_doc = sorted(v1 - doc_heads)

    print(f"registered_routes={len(routes)}")
    print(f"inventory_rows={len(inventory)}")
    print(f"frontend_v1_headings={len(doc_heads)}")
    if missing_from_inventory:
        print("MISSING_FROM_INVENTORY")
        for row in missing_from_inventory:
            print(f"  {row[0]} {row[1]}")
    if extra_in_inventory:
        print("EXTRA_IN_INVENTORY")
        for row in extra_in_inventory:
            print(f"  {row[0]} {row[1]}")
    if missing_from_doc:
        print("MISSING_FROM_FRONTEND_DOC")
        for row in missing_from_doc:
            print(f"  {row[0]} {row[1]}")
    sample_errors = validate_sample_models()
    if sample_errors:
        print("INVALID_DOCUMENTED_SAMPLES")
        for row in sample_errors:
            print(f"  {row}")
    ok = (
        not missing_from_inventory
        and not extra_in_inventory
        and not missing_from_doc
        and not sample_errors
    )
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""School dashboard: auth, tenant isolation, and real calculations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from dashboard.cache import cache
from dashboard.events import bus
from dashboard.metrics import classify_quadrant
from database import get_db
from services.users import issue_access_token
from tests.dashboard_memory import MemoryDB

NOW = datetime(2026, 6, 15, tzinfo=timezone.utc)


def _auth(user_id: str) -> dict:
    return {"Authorization": "Bearer " + issue_access_token(user_id)}


def _user(**fields):
    base = {
        "isActive": True,
        "roles": ["student"],
        "school": "Riverdale School",
    }
    base.update(fields)
    return base


def _mark(student_id, subject, percentage, when):
    return {
        "student_id": student_id,
        "subject": subject,
        "percentage": percentage,
        "marks": percentage,
        "total_marks": 100,
        "exam_date": when,
        "exam_type": "Unit Test",
    }


def seed(db: MemoryDB) -> None:
    users = db["users"]
    users.docs.extend(
        [
            _user(
                user_id="prin_river",
                name="River Principal",
                email="principal@riverdale.test",
                roles=["principal"],
                board="CBSE",
            ),
            _user(
                user_id="teach_river",
                name="River Teacher",
                email="teacher@riverdale.test",
                roles=["teacher"],
                subjects=["Physics"],
                classes=["10"],
            ),
            _user(
                user_id="counsel_river",
                name="River Counselor",
                email="counselor@riverdale.test",
                roles=["counselor"],
            ),
            _user(user_id="stu_star", name="Star Student", class_="10", email="star@riverdale.test", attendance_percentage=92),
            _user(user_id="stu_plateau", name="Plateau Student", class_="10"),
            _user(user_id="stu_climb", name="Climber Student", class_="10", current_risk_level="low"),
            _user(user_id="stu_critical", name="Critical Student", class_="10", current_risk_level="high"),
            _user(
                user_id="stu_migrated",
                name="Migrated Kid",
                class_="10",
                school_id="sch_other",
            ),
            _user(
                user_id="prin_harbor",
                name="Harbor Principal",
                email="principal@harbor.test",
                roles=["principal"],
                school="Harbor School",
            ),
            _user(
                user_id="stu_harbor",
                name="HarborOnly Student",
                class_="10",
                school="Harbor School",
                current_risk_level="critical",
            ),
        ]
    )
    # class_ is a keyword workaround — rewrite the field.
    for doc in users.docs:
        if "class_" in doc:
            doc["class"] = doc.pop("class_")
    db["marks"].docs.extend(
        [
            _mark("stu_star", "Physics", 80, NOW - timedelta(days=20)),
            _mark("stu_star", "Physics", 88, NOW),
            _mark("stu_plateau", "Physics", 92, NOW - timedelta(days=20)),
            _mark("stu_plateau", "Physics", 80, NOW),
            _mark("stu_climb", "Physics", 40, NOW - timedelta(days=20)),
            _mark("stu_climb", "Physics", 55, NOW),
            _mark("stu_critical", "Physics", 50, NOW - timedelta(days=20)),
            _mark("stu_critical", "Physics", 30, NOW),
            _mark("stu_harbor", "Physics", 99, NOW),
        ]
    )
    db["mood_logs"].docs.append(
        {
            "user_id": "stu_star",
            "mood": "calm",
            "score": 8,
            "note": "PRIVATE_MOOD_NOTE",
            "logged_at": datetime.now(timezone.utc),
            "created_at": datetime.now(timezone.utc),
        }
    )
    db["sleep_logs"].docs.append(
        {
            "user_id": "stu_star",
            "total_duration_minutes": 480,
            "created_at": NOW,
            "date": NOW.date(),
        }
    )
    db["user_patterns"].docs.append(
        {
            "user_id": "stu_plateau",
            "pattern_type": "academic_decline",
            "domains": ["academic"],
            "status": "ESTABLISHED",
            "confidence": 0.8,
            "description": "SECRET_PATTERN_TEXT",
            "last_observed_at": NOW,
        }
    )
    db["user_risk_turns"].docs.append(
        {
            "user_id": "stu_critical",
            "risk_intensity_score": 9.1,
            "crisis_keywords": False,
            "created_at": NOW,
        }
    )
    db["journal_entries"].docs.append(
        {"user_id": "stu_star", "content": "SECRET_JOURNAL_BODY"}
    )
    db["student_psychological_profiles"].docs.append(
        {
            "user_id": "stu_climb",
            "attendance": {"attendance_percentage": 80},
            "journaling": {"journal_summary": "SECRET_PROFILE_JOURNAL"},
        }
    )


@pytest.fixture
def api():
    db = MemoryDB()
    seed(db)
    app.dependency_overrides[get_db] = lambda: db
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    cache.clear()
    bus.reset()
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        with TestClient(app) as client:
            yield client, db
    app.dependency_overrides.clear()
    cache.clear()
    bus.reset()


def test_quadrant_uses_existing_mark_cutoffs():
    assert classify_quadrant(88, 8) == "stars"
    assert classify_quadrant(80, -12) == "plateaued"
    assert classify_quadrant(55, 15) == "climbers"
    assert classify_quadrant(30, -20) == "critical"
    assert classify_quadrant(70, None) is None


def test_events_stay_inside_one_school():
    bus.reset()
    river = bus.subscribe("name:Riverdale School")
    harbor = bus.subscribe("name:Harbor School")
    bus.publish(
        "name:Riverdale School",
        "student_risk_changed",
        student_id="stu_critical",
        severity="critical",
    )
    event = river.get_nowait()
    assert event["event"] == "student_risk_changed"
    assert event["school_id"] == "name:Riverdale School"
    assert event["severity"] == "critical"
    assert harbor.empty()
    bus.reset()


def test_dashboard_requires_a_principal(api):
    client, _ = api
    assert client.get("/api/v1/dashboard/overview").status_code == 401
    denied = client.get("/api/v1/dashboard/overview", headers=_auth("stu_star"))
    assert denied.status_code == 403
    teacher = client.get("/api/v1/dashboard/overview", headers=_auth("teach_river"))
    assert teacher.status_code == 403
    missing_school = client
    # health remains public and unversioned routes are untouched
    assert client.get("/health/live").status_code == 200


def test_overview_is_school_scoped_and_not_hardcoded(api):
    client, _ = api
    response = client.get(
        "/api/v1/dashboard/overview",
        headers=_auth("prin_river"),
        params={"school_id": "name:Harbor School"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["meta"]["request_id"]
    data = body["data"]
    assert data["school"]["name"] == "Riverdale School"
    assert data["counts"]["students"] == 4
    assert data["counts"]["teachers"] == 1
    names = {row["name"] for row in data["top_performers"]}
    assert "HarborOnly Student" not in names
    assert "Migrated Kid" not in names
    assert data["academic_health"]["passing_rate"] is None
    assert data["school_growth"]["parent_nps"]["available"] is False
    assert data["teachers"][0]["rating"] is None
    assert data["school_health"]["mental"]["value"] == 80
    assert data["risk"]["critical"] == 1
    assert data["risk"]["watch"] == 1
    assert data["academic_health"]["index"] != 84
    assert data["school_health"]["index"] != 84


def test_harbor_principal_cannot_see_riverdale_students(api):
    client, _ = api
    overview = client.get("/api/v1/dashboard/overview", headers=_auth("prin_harbor")).json()["data"]
    assert overview["counts"]["students"] == 1
    assert overview["top_performers"][0]["name"] == "HarborOnly Student"
    missing = client.get("/api/v1/dashboard/students/stu_star/profile", headers=_auth("prin_harbor"))
    assert missing.status_code == 404
    own = client.get("/api/v1/dashboard/students/stu_star/profile", headers=_auth("prin_river"))
    assert own.status_code == 200
    profile = own.json()["data"]
    assert "SECRET_JOURNAL_BODY" not in own.text
    assert "SECRET_PATTERN_TEXT" not in own.text
    assert "PRIVATE_MOOD_NOTE" not in own.text
    assert profile["academic"]["latest_percentage"] == 88
    assert profile["attendance"]["value"] == 92


def test_class_quadrant_and_pagination(api):
    client, _ = api
    headers = _auth("prin_river")
    quadrant = client.get(
        "/api/v1/dashboard/classes/10/performance-quadrant",
        headers=headers,
    )
    assert quadrant.status_code == 200
    data = quadrant.json()["data"]
    assert data["stars"]["count"] == 1
    assert data["plateaued"]["count"] == 1
    assert data["climbers"]["count"] == 1
    assert data["critical"]["count"] == 1
    assert data["stars"]["students"][0]["student_id"] == "stu_star"
    assert data["method"]["id"] == "marks_delta_quadrant_v1"
    other = client.get(
        "/api/v1/dashboard/classes/10/performance-quadrant",
        headers=_auth("prin_harbor"),
    )
    assert other.json()["data"]["stars"]["count"] == 0
    missing = client.get("/api/v1/dashboard/classes/9/overview", headers=headers)
    assert missing.status_code == 404

    seen = []
    cursor = None
    for _ in range(6):
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = client.get(
            "/api/v1/dashboard/classes/10/students",
            headers=headers,
            params=params,
        )
        body = page.json()
        if not body["data"]:
            assert body["meta"]["has_next"] is False
            break
        seen.append(body["data"][0]["student_id"])
        cursor = body["meta"]["next_cursor"]
        if not body["meta"]["has_next"]:
            break
    assert seen == ["stu_climb", "stu_critical", "stu_plateau", "stu_star"]
    empty = client.get(
        "/api/v1/dashboard/classes/10/students",
        headers=headers,
        params={"page": 9, "limit": 25},
    )
    assert empty.json()["data"] == []
    assert empty.json()["meta"]["has_next"] is False


def test_subject_risk_and_teacher_do_not_invent_ratings(api):
    client, _ = api
    headers = _auth("prin_river")
    subject = client.get(
        "/api/v1/dashboard/classes/10/subjects/physics/overview",
        headers=headers,
    )
    assert subject.status_code == 200
    payload = subject.json()["data"]
    assert payload["teacher"]["teacher_id"] == "teach_river"
    assert payload["teacher"]["rating"] is None
    assert payload["student_feedback"]["available"] is False
    assert payload["concept_mastery"]["available"] is False
    assert payload["class_average"] is not None
    risk = client.get("/api/v1/dashboard/risk/students", headers=headers).json()
    ids = {row["student_id"] for row in risk["data"]}
    assert ids == {"stu_critical", "stu_plateau"}
    assert "stu_harbor" not in ids
    review = client.post(
        "/api/v1/dashboard/teachers/teach_river/reviews",
        headers=headers,
        json={"notes": "Focus on lab explanations.", "focus_areas": ["labs"], "rating": 5},
    )
    assert review.status_code == 400
    saved = client.post(
        "/api/v1/dashboard/teachers/teach_river/reviews",
        headers=headers,
        json={"notes": "Focus on lab explanations.", "focus_areas": ["labs"]},
    )
    assert saved.status_code == 201
    assert saved.json()["data"]["rating"] is None
    hidden = client.get("/api/v1/dashboard/teachers/teach_river", headers=_auth("prin_harbor"))
    assert hidden.status_code == 404


def test_interventions_notifications_settings_and_reports(api):
    client, db = api
    headers = _auth("prin_river")
    created = client.post(
        "/api/v1/dashboard/interventions",
        headers=headers,
        json={
            "student_id": "stu_critical",
            "title": "Physics recovery plan",
            "plan": "Short check-ins after the next unit test.",
            "assignee_user_id": "counsel_river",
        },
    )
    assert created.status_code == 201
    intervention_id = created.json()["data"]["intervention_id"]
    blocked = client.get(
        f"/api/v1/dashboard/interventions/{intervention_id}",
        headers=_auth("prin_harbor"),
    )
    assert blocked.status_code == 404
    patched = client.patch(
        f"/api/v1/dashboard/interventions/{intervention_id}",
        headers=headers,
        json={"status": "in_progress"},
    )
    assert patched.json()["data"]["status"] == "in_progress"
    listed = client.get(
        "/api/v1/dashboard/students/stu_critical/interventions",
        headers=headers,
    )
    assert listed.json()["meta"]["total"] == 1

    contact = client.post(
        "/api/v1/dashboard/students/stu_critical/parent-contact",
        headers=headers,
        json={"message": "Please call the school office."},
    )
    assert contact.status_code == 200
    assert contact.json()["data"]["delivered"] is False
    inbox = client.get("/api/v1/dashboard/notifications", headers=headers).json()
    assert inbox["meta"]["total"] >= 1
    note_id = inbox["data"][0]["notification_id"]
    other_inbox = client.get("/api/v1/dashboard/notifications", headers=_auth("prin_harbor")).json()
    assert all(row["notification_id"] != note_id for row in other_inbox["data"])
    read = client.post(f"/api/v1/dashboard/notifications/{note_id}/read", headers=headers)
    assert read.json()["data"]["read"] is True
    client.post("/api/v1/dashboard/notifications/read-all", headers=headers)

    settings = client.patch(
        "/api/v1/dashboard/settings",
        headers=headers,
        json={"ai_insights": False},
    )
    assert settings.json()["data"]["ai_insights"] is False
    assert settings.json()["data"]["persisted"] is True
    again = client.get("/api/v1/dashboard/settings", headers=headers)
    assert again.json()["data"]["ai_insights"] is False

    report = client.post(
        "/api/v1/dashboard/reports",
        headers=headers,
        json={"type": "board", "scope": "school", "include_charts": True},
    )
    assert report.status_code == 202
    report_id = report.json()["data"]["report_id"]
    ready = client.get(f"/api/v1/dashboard/reports/{report_id}", headers=headers)
    assert ready.json()["data"]["status"] == "ready"
    preview = client.get(f"/api/v1/dashboard/reports/{report_id}/preview", headers=headers)
    assert preview.status_code == 200
    download = client.get(f"/api/v1/dashboard/reports/{report_id}/download", headers=headers)
    assert download.status_code == 200
    assert "HarborOnly" not in download.text
    foreign = client.get(f"/api/v1/dashboard/reports/{report_id}/download", headers=_auth("prin_harbor"))
    assert foreign.status_code == 404
    assert db["dashboard_audit"].docs


def test_assistant_context_excludes_private_and_other_schools(api):
    client, _ = api
    headers = _auth("prin_river")
    with patch(
        "dashboard.services.assistant_service.invoke_llm",
        return_value="Risk is based on stored bands.",
    ) as mocked:
        response = client.post(
            "/api/v1/dashboard/assistant",
            headers=headers,
            json={"message": "Why is risk up in grade 10?", "context": {"grade_id": "10"}},
        )
    assert response.status_code == 200
    prompt = mocked.call_args.args[0]
    assert "SECRET_JOURNAL_BODY" not in prompt
    assert "PRIVATE_MOOD_NOTE" not in prompt
    assert "HarborOnly Student" not in prompt
    assert "Star Student" not in prompt
    assert "Riverdale School" in prompt
    client.patch(
        "/api/v1/dashboard/settings",
        headers=headers,
        json={"ai_insights": False},
    )
    blocked = client.post(
        "/api/v1/dashboard/assistant",
        headers=headers,
        json={"message": "Hello"},
    )
    assert blocked.status_code == 403


def test_analytics_leave_benchmarks_empty(api):
    client, _ = api
    headers = _auth("prin_river")
    comparative = client.get("/api/v1/dashboard/analytics/comparative", headers=headers).json()["data"]
    assert comparative["available"] is False
    assert comparative["benchmark"] is None
    assert comparative["school"]["student_count"] == 4
    trends = client.get("/api/v1/dashboard/analytics/grade-trends", headers=headers).json()["data"]
    assert trends["grades"][0]["grade_id"] == "10"
    wellbeing = client.get("/api/v1/dashboard/analytics/wellbeing", headers=headers).json()["data"]
    assert "PRIVATE_MOOD_NOTE" not in str(wellbeing)
    context = client.get("/api/v1/dashboard/context", headers=headers).json()["data"]
    assert context["role"] == "principal"
    assert context["school"]["id"] == "name:Riverdale School"


def test_counselor_can_open_the_dashboard_and_indexes_are_named(api):
    client, _ = api
    response = client.get("/api/v1/dashboard/context", headers=_auth("counsel_river"))
    assert response.status_code == 200
    import asyncio

    from dashboard.indexes import ensure_dashboard_indexes
    from tests.dashboard_memory import MemoryDB as DB

    recorded = DB()
    asyncio.run(ensure_dashboard_indexes(recorded))
    names = [spec[1] for spec in recorded["marks"].indexes]
    assert "marks_student_exam_date" in names
    assert "dashboard_intervention_school_id" in [
        spec[1] for spec in recorded["dashboard_interventions"].indexes
    ]
    from core.rate_limit import _limit_for

    assert _limit_for("/api/v1/dashboard/assistant")[0] <= 20

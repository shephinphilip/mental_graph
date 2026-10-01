"""Session payload stays identity-only. Language, timezone, and report concerns."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app import app
from backend_core.users import hash_password, issue_access_token, public_user_view
from database import get_db
from reports.concerns import build_key_concerns, local_timestamp
from services.context import _fetch_profile_and_structured_context
from tests.test_tracking import _db


def _client(fake):
    app.dependency_overrides[get_db] = lambda: fake
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    return TestClient(app, raise_server_exceptions=False), mongo


def _auth(user_id: str = "user_a"):
    return {"Authorization": "Bearer " + issue_access_token(user_id)}


def _account(fake, **extra):
    doc = {
        "_id": extra.get("user_id", "user_a"),
        "user_id": "user_a",
        "email": "a@school.edu",
        "name": "Asha Rao",
        "password": hash_password("password123"),
        "isActive": True,
        "class": "10",
        "school": "Demo School",
        "board": "CBSE",
        "preferred_language": "ENGLISH",
        "personalization_consent": False,
        "age": 15,
        "chief_concern": "Exam anxiety",
    }
    doc.update(extra)
    fake["users"].docs.append(doc)
    return doc


def test_public_view_does_not_carry_mental_health_fields():
    view = public_user_view(
        {
            "user_id": "user_a",
            "age": 15,
            "chief_concern": "Exam anxiety",
            "timezone": "Asia/Kolkata",
        }
    )
    assert "age" not in view
    assert "chief_concern" not in view
    assert view["timezone"] == "Asia/Kolkata"


def test_login_omits_age_and_chief_concern_and_allows_a_missing_timezone():
    fake = _db()
    fake["users"].docs.clear()
    _account(fake)
    client, mongo = _client(fake)
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        response = client.post(
            "/auth/login",
            json={"email": "a@school.edu", "password": "password123"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert "age" not in body
    assert "chief_concern" not in body
    assert body["timezone"] is None
    assert body["preferred_language"] == "ENGLISH"
    assert body["student_class"] == "10"
    assert "exam" not in body["access_token"].lower()


def test_signup_omits_age_and_chief_concern():
    fake = _db()
    client, mongo = _client(fake)
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        response = client.post(
            "/auth/signup",
            json={"email": "new.student@school.edu", "password": "password123", "name": "New Student"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == 201
    body = response.json()
    assert "age" not in body
    assert "chief_concern" not in body
    assert body["timezone"] is None
    stored = next(doc for doc in fake["users"].docs if doc.get("email") == "new.student@school.edu")
    assert "age" not in stored
    assert "chief_concern" not in stored
    assert "timezone" not in stored


def test_language_update_writes_only_the_token_user():
    fake = _db()
    fake["users"].docs.clear()
    _account(fake)
    _account(fake, _id="user_b", user_id="user_b", email="b@school.edu", preferred_language="ENGLISH")
    client, mongo = _client(fake)
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        response = client.post(
            "/api/language",
            headers=_auth("user_a"),
            json={"preferred_language": "MALAYALAM", "user_id": "user_b"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "user_id": "user_a",
        "preferred_language": "MALAYALAM",
    }
    owner = next(doc for doc in fake["users"].docs if doc["user_id"] == "user_a")
    other = next(doc for doc in fake["users"].docs if doc["user_id"] == "user_b")
    assert owner["preferred_language"] == "MALAYALAM"
    assert owner["preferred_language_updated_at"]
    assert owner["updated_at"]
    assert other["preferred_language"] == "ENGLISH"


def test_timezone_update_rejects_abbreviations_and_writes_the_token_user():
    fake = _db()
    fake["users"].docs.clear()
    _account(fake)
    _account(fake, _id="user_b", user_id="user_b", email="b@school.edu")
    client, mongo = _client(fake)
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ):
        rejected = client.post(
            "/api/timezone",
            headers=_auth("user_a"),
            json={"timezone": "IST"},
        )
        offset = client.post(
            "/api/timezone",
            headers=_auth("user_a"),
            json={"timezone": "GMT+5:30"},
        )
        saved = client.post(
            "/api/timezone",
            headers=_auth("user_a"),
            json={"timezone": "Asia/Kolkata", "user_id": "user_b"},
        )
    app.dependency_overrides.clear()
    assert rejected.status_code == 400
    assert offset.status_code == 400
    assert saved.status_code == 200
    assert saved.json() == {
        "success": True,
        "user_id": "user_a",
        "timezone": "Asia/Kolkata",
    }
    owner = next(doc for doc in fake["users"].docs if doc["user_id"] == "user_a")
    other = next(doc for doc in fake["users"].docs if doc["user_id"] == "user_b")
    assert owner["timezone"] == "Asia/Kolkata"
    assert owner["updated_at"]
    assert "timezone" not in other


def test_key_concerns_use_the_user_message_clock_and_ignore_the_assistant():
    moment = datetime(2026, 10, 1, 16, 15, 32, tzinfo=timezone.utc)
    later = datetime(2026, 10, 1, 16, 40, 18, tzinfo=timezone.utc)
    concerns = build_key_concerns(
        [
            {
                "role": "user",
                "content": "I have been feeling anxious about my exams and I cannot concentrate.",
                "created_at": moment,
            },
            {
                "role": "assistant",
                "content": "You may be experiencing performance anxiety.",
                "created_at": later,
            },
            {
                "role": "user",
                "content": "I am afraid of disappointing my parents.",
                "created_at": later,
            },
        ],
        "Asia/Kolkata",
    )
    assert local_timestamp(moment, "Asia/Kolkata") == "2026-10-01T21:45:32+05:30"
    assert concerns["2026-10-01T21:45:32+05:30"] == [
        "Exam anxiety",
        "Difficulty concentrating",
    ]
    assert concerns["2026-10-01T22:10:18+05:30"] == ["Fear of disappointing parents"]
    assert build_key_concerns(
        [{"role": "user", "content": "I feel anxious about exams.", "created_at": moment}],
        None,
    ) == {}


@pytest.mark.asyncio
async def test_report_stores_key_concerns_for_the_token_user_only():
    from services.session_report import generate_session_report

    fake = _db()
    owner = next(doc for doc in fake["users"].docs if doc["user_id"] == "user_a")
    owner["timezone"] = "Asia/Kolkata"
    moment = datetime(2026, 10, 1, 16, 15, 32, tzinfo=timezone.utc)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"summary": "The exams were on their mind.",'
                '"valence": -0.2, "arousal": 0.4, "dominance": 0.0, "confidence": 0.5,'
                '"latent_states": [], "crisis_signal": false}'
            )
        )
    )

    async def _load(_db_handle, user_id, session_id, limit=40):
        if user_id != "user_a":
            return []
        return [
            {
                "role": "user",
                "content": "I have been feeling anxious about my exams.",
                "created_at": moment,
                "message_kind": "chat",
            },
            {
                "role": "assistant",
                "content": "You may be experiencing performance anxiety.",
                "created_at": moment,
                "message_kind": "chat",
            },
        ]

    with patch("services.session_report.load_session_messages", _load), patch(
        "llm_provider.get_llm", return_value=llm
    ), patch(
        "services.patterns.retrieve.retrieve_relevant_patterns",
        AsyncMock(return_value=[]),
    ):
        report = await generate_session_report(fake, user_id="user_a", session_id="sess")
        with pytest.raises(ValueError):
            await generate_session_report(fake, user_id="user_b", session_id="sess")

    assert report["key_concerns"] == {
        "2026-10-01T21:45:32+05:30": ["Exam anxiety"],
    }
    assert report["timezone"] == "Asia/Kolkata"
    stored = fake["session_reports"].docs
    assert len(stored) == 1
    assert stored[0]["user_id"] == "user_a"
    assert stored[0]["key_concerns"]["2026-10-01T21:45:32+05:30"] == ["Exam anxiety"]
    assert "performance anxiety" not in str(stored[0]["key_concerns"]).lower()


@pytest.mark.asyncio
async def test_chat_profile_still_reads_stored_age_and_chief_concern():
    fake = _db()
    owner = next(doc for doc in fake["users"].docs if doc["user_id"] == "user_a")
    owner["age"] = 15
    owner["chief_concern"] = "Exam anxiety"
    profile = await _fetch_profile_and_structured_context(fake, "user_a")
    assert "Age: 15" in profile["user_profile"]
    assert "Chief concern on file: Exam anxiety" in profile["user_profile"]

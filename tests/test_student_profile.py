"""Derived student profile: one document, no fabricated source data."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import HTTPException

from prompts import format_system_prompt
from services.apm import contains_crisis_signal
from services.student_profile import (
    assemble_profile,
    consolidate_student_profile,
    delete_student_profile,
    format_profile_context,
    get_student_profile_context,
    trend_of,
)

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        self._docs = self._docs[:count]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []
        self.indexes: List[Any] = []

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [doc for doc in self.docs if _match(doc, query or {})]
        return found[0] if found else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace()

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _match(doc, query):
                doc.update(update.get("$set") or {})
                for field in (update.get("$unset") or {}):
                    doc.pop(field, None)
                return SimpleNamespace(modified_count=1)
        if upsert:
            created = {key: value for key, value in (query or {}).items() if not isinstance(value, dict)}
            created.update(update.get("$set") or {})
            self.docs.append(created)
            return SimpleNamespace(modified_count=0, upserted_id=1)
        return SimpleNamespace(modified_count=0)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [doc for doc in self.docs if not _match(doc, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def count_documents(self, query):
        return sum(1 for doc in self.docs if _match(doc, query))

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs))


def _match(doc, query):
    if "$or" in query:
        return any(_match(doc, clause) for clause in query["$or"])
    for key, value in query.items():
        if key.startswith("$"):
            continue
        current = doc.get(key)
        if isinstance(value, dict):
            if "$ne" in value and current == value["$ne"]:
                return False
            if "$in" in value and current not in value["$in"]:
                return False
            if "$gte" in value and (current is None or current < value["$gte"]):
                return False
            if "$in" not in value and "$ne" not in value and "$gte" not in value:
                return False
            continue
        if current != value:
            return False
    return True


class _DB:
    def __init__(self):
        self._cols: Dict[str, _Collection] = {}

    def __getitem__(self, name):
        self._cols.setdefault(name, _Collection())
        return self._cols[name]


def _user(user_id="usr_a", **extra):
    base = {
        "user_id": user_id,
        "name": "Meera",
        "age": 16,
        "class": "11",
        "city": "Pune",
        "school": "City School",
        "board": "CBSE",
        "preferred_language": "HINDI",
        "personalization_consent": True,
        "isActive": True,
        "chief_concern": "Physics tests",
    }
    base.update(extra)
    return base


def _moods(labels):
    rows = []
    for index, label in enumerate(labels):
        rows.append(
            {
                "value": label,
                "stressed": label in {"stressed", "anxious", "overwhelmed", "sad", "low"},
                "at": f"2026-09-{index + 1:02d}",
                "source": "mood_logs",
            }
        )
    return rows


def test_profile_creation_and_upsert_shape():
    profile = assemble_profile(_user(), {}, [], [], personalization=True, now=NOW)
    assert profile["user_id"] == "usr_a"
    assert profile["profile_version"] == 1
    assert profile["identity"]["name"] == "Meera"
    assert profile["identity"]["preferred_language"] == "HINDI"
    assert profile["academics"]["recent_marks_percentage"] is None
    assert profile["attendance"]["attendance_percentage"] is None
    assert profile["psychological_pattern_scores"]["stress"]["score"] is None


def test_missing_optional_data_stays_empty():
    profile = assemble_profile(_user(name=""), {}, [], [], personalization=True, now=NOW)
    assert profile["sleep"]["average_sleep_hours"] is None
    assert profile["journaling"]["journal_summary"] == ""
    assert profile["meditation"]["total_completed_sessions"] == 0
    assert profile["academics"]["academic_pattern_summary"] == ""
    assert profile["attendance"]["recent_absence_reason"] == ""


def test_journal_integration_uses_selected_mood_and_topics():
    rows = [
        {"mood": "😢", "topics": ["exam", "sleep"], "date": "2026-09-01"},
        {"mood": "😢", "topics": ["exam"], "date": "2026-09-02"},
        {"mood": "😊", "topics": ["friend"], "date": "2026-09-03"},
    ]
    profile = assemble_profile(
        _user(), {"journaling": rows}, [], [], personalization=True, now=NOW
    )
    assert profile["journaling"]["journal_entry_count_recent"] == 3
    assert "exam" in profile["journaling"]["recurring_journal_themes"]
    assert "😢" in profile["journaling"]["journal_mood_pattern"]
    assert "anxiety disorder" not in profile["journaling"]["journal_summary"].lower()


def test_sleep_integration_describes_logs_without_causation():
    rows = [
        {"value": 300, "date": "2026-09-01"},
        {"value": 310, "date": "2026-09-02"},
        {"value": 290, "date": "2026-09-03"},
        {"value": 280, "date": "2026-09-04"},
    ]
    profile = assemble_profile(_user(), {"sleep": rows}, [], [], personalization=True, now=NOW)
    assert profile["sleep"]["sleep_evidence_count"] == 4
    assert profile["sleep"]["average_sleep_hours"] is not None
    assert "does not explain why" in profile["sleep"]["sleep_pattern"]
    assert profile["psychological_pattern_scores"]["sleep_disruption"]["score"] is not None


def test_mood_integration_needs_enough_check_ins():
    thin = assemble_profile(
        _user(), {"mood": _moods(["stressed", "calm"])}, [], [], personalization=True, now=NOW
    )
    assert thin["psychological_pattern_scores"]["stress"]["score"] is None
    assert thin["psychological_pattern_scores"]["stress"]["trend"] == "insufficient_data"
    enough = assemble_profile(
        _user(),
        {"mood": _moods(["calm", "calm", "stressed", "anxious"])},
        [],
        [],
        personalization=True,
        now=NOW,
    )
    block = enough["psychological_pattern_scores"]["stress"]
    assert block["score"] == 5
    assert block["evidence_count"] == 4
    assert block["confidence"] == 0.5
    assert block["trend"] == "increasing"


def test_academic_integration_and_missing_marks():
    empty = assemble_profile(_user(), {}, [], [], personalization=True, now=NOW)
    assert empty["academics"]["recent_marks_percentage"] is None
    assert empty["academics"]["marks_trend"] == "insufficient_data"
    rows = [
        {"value": 80, "subject": "Physics", "at": "2026-01-01"},
        {"value": 70, "subject": "Physics", "at": "2026-02-01"},
        {"value": 60, "subject": "Physics", "at": "2026-03-01"},
        {"value": 40, "subject": "Physics", "at": "2026-04-01"},
    ]
    profile = assemble_profile(_user(), {"academic": rows}, [], [], personalization=True, now=NOW)
    assert profile["academics"]["recent_marks_percentage"] == 40
    assert profile["academics"]["marks_trend"] == "decreasing"
    assert "Physics" in profile["academics"]["academic_struggles"]
    assert "disorder" not in profile["academics"]["academic_pattern_summary"]


def test_attendance_integration_and_missing_source():
    missing = assemble_profile(_user(), {}, [], [], personalization=True, now=NOW)
    assert missing["attendance"]["attendance_percentage"] is None
    assert missing["attendance"]["recent_absence_reason"] == ""
    present = assemble_profile(
        _user(),
        {"attendance": [{"value": 86.5, "source": "users"}]},
        [],
        [],
        personalization=True,
        now=NOW,
    )
    assert present["attendance"]["attendance_percentage"] == 86.5
    assert present["attendance"]["attendance_trend"] == "insufficient_data"
    reasoned = assemble_profile(
        _user(),
        {"attendance": [{"value": {"percentage": 70, "recent_absence_reason": "fever"}}]},
        [],
        [],
        personalization=True,
        now=NOW,
    )
    assert reasoned["attendance"]["recent_absence_reason"] == "fever"


def test_meditation_feedback_is_explicit_only():
    executions = [
        {"status": "STARTED", "meditation_id": "breath_2", "user_helpfulness_feedback": None},
        {"status": "COMPLETED", "meditation_id": "breath_2", "user_helpfulness_feedback": "HELPFUL", "completed_at": NOW},
        {"status": "COMPLETED", "meditation_id": "body_8", "user_helpfulness_feedback": "NOT_HELPFUL", "completed_at": NOW},
    ]
    profile = assemble_profile(_user(), {}, [], executions, personalization=True, now=NOW)
    assert profile["meditation"]["total_completed_sessions"] == 2
    assert "breath_2" in profile["interventions"]["helpful_interventions"]
    assert "body_8" in profile["interventions"]["unhelpful_interventions"]
    assert "class" not in profile["meditation"]["meditation_response_pattern"].lower()
    assert profile["psychological_pattern_scores"]["coping_difficulty"]["score"] is None


def test_tasks_and_habits_do_not_prove_recovery():
    profile = assemble_profile(
        _user(),
        {
            "tasks": [
                {"title": "Two physics questions", "incomplete": True, "pending": 1},
                {"title": "Pack the bag", "incomplete": False, "pending": 0},
            ],
            "habits": [
                {"title": "Water", "value": "active"},
                {"title": "Stretch", "value": "paused"},
            ],
        },
        [],
        [],
        personalization=True,
        now=NOW,
    )
    assert profile["tasks_and_habits"]["active_tasks"] == ["Two physics questions"]
    assert "not evidence of psychological recovery" in profile["tasks_and_habits"]["task_completion_pattern"]
    assert "Stretch" in profile["tasks_and_habits"]["struggling_habits"]
    assert profile["interventions"]["recovery_patterns"] == []


def test_recent_sessions_topics_and_resolution():
    reports = []
    for index in range(12):
        reports.append(
            {
                "session_id": f"s{index}",
                "created_at": f"2026-09-{index + 1:02d}",
                "psychiatric_summary": "Physics mock still felt unfinished.",
                "crisis_signal": False,
                "events": [
                    {"label": "Physics mock", "resolved": index >= 10},
                    {"label": "Argument with dad", "resolved": False},
                ],
            }
        )
    profile = assemble_profile(_user(), {}, reports, [], personalization=True, now=NOW)
    assert len(profile["conversations"]["recent_sessions"]) == 10
    assert "Physics mock" in profile["conversations"]["recurring_conversation_topics"]
    assert "Physics mock" in profile["conversations"]["unresolved_issues"]
    assert profile["conversations"]["recent_session_summary"] == "Physics mock"
    assert "unfinished" not in str(profile)
    assert "transcript" not in profile["conversations"]["recent_session_summary"].lower()


def test_trend_and_insufficient_evidence():
    assert trend_of([8, 8, 7, 6]) == "decreasing"
    assert trend_of([5, 5, 6, 6]) == "increasing"
    assert trend_of([5, 5, 5, 5]) == "stable"
    assert trend_of([5, 5]) == "insufficient_data"
    profile = assemble_profile(
        _user(), {"mood": _moods(["sad"])}, [], [], personalization=True, now=NOW
    )
    assert profile["psychological_pattern_scores"]["emotional_distress"]["score"] is None
    assert profile["psychological_pattern_scores"]["overall_distress"]["score"] is None


def test_current_message_and_crisis_override_profile():
    profile = assemble_profile(
        _user(),
        {"mood": _moods(["calm", "calm", "calm", "calm"])},
        [{"session_id": "s", "created_at": NOW, "crisis_signal": False, "events": []}],
        [],
        personalization=True,
        now=NOW,
    )
    context = format_profile_context(profile, personalization=True)
    assert "current message" in context.lower()
    assert "no_crisis" not in context
    prompt = format_system_prompt(student_profile_context=context)
    assert "not diagnoses" in prompt.lower() or "not a diagnosis" in prompt.lower()
    assert "YOU ARE NEVER THE CRISIS SYSTEM" in prompt
    assert contains_crisis_signal("I want to die") is True


def test_consent_off_hides_derived_personalization():
    profile = assemble_profile(
        _user(personalization_consent=False),
        {"journaling": [{"mood": "😢", "topics": ["exam", "exam"], "date": "2026-09-01"}]},
        [{"session_id": "s", "psychiatric_summary": "Secret worry", "events": [{"label": "Secret worry", "resolved": False}]}],
        [{"status": "COMPLETED", "meditation_id": "breath_2", "user_helpfulness_feedback": "HELPFUL"}],
        personalization=False,
        now=NOW,
    )
    assert profile["psychological_pattern_scores"]["stress"]["score"] is None
    assert profile["conversations"]["recent_session_summary"] == ""
    assert profile["interventions"]["helpful_interventions"] == []
    context = format_profile_context(profile, personalization=False)
    assert "Personalization consent is off" in context
    assert "Secret worry" not in context
    assert "/10" not in context


@pytest.mark.asyncio
async def test_consolidate_is_idempotent_isolated_and_deletes_only_profile():
    db = _DB()
    db["users"].docs.append(_user())
    db["users"].docs.append(_user("usr_b", name="Arun", chief_concern=""))
    db["journal_entries"].docs.append(
        {
            "user_id": "usr_b",
            "title": "secret-topic-b",
            "content": "secret-topic-b only",
            "mood": "😢",
            "timestamp": NOW,
            "tags": ["secret-topic-b"],
        }
    )
    db["session_reports"].docs.extend(
        [
            {"user_id": "usr_a", "session_id": "a1", "created_at": NOW, "psychiatric_summary": "Meera reading", "events": []},
            {"user_id": "usr_b", "session_id": "b1", "created_at": NOW, "psychiatric_summary": "Arun reading", "events": [{"label": "secret-topic-b", "resolved": False}]},
        ]
    )
    first = await consolidate_student_profile(db, "usr_a", now=NOW)
    second = await consolidate_student_profile(db, "usr_a", now=NOW)
    stored = db["student_psychological_profiles"].docs
    assert len(stored) == 1
    assert first["metadata"]["data_completeness"] == second["metadata"]["data_completeness"]
    assert "secret-topic-b" not in str(stored[0])
    assert "Arun reading" not in str(stored[0])
    assert stored[0]["conversations"]["session_count"] == 1
    await asyncio.gather(
        consolidate_student_profile(db, "usr_a", now=NOW),
        consolidate_student_profile(db, "usr_a", now=NOW),
    )
    assert len(db["student_psychological_profiles"].docs) == 1
    journal_before = len(db["journal_entries"].docs)
    deleted = await delete_student_profile(db, "usr_a")
    assert deleted == 1
    assert len(db["journal_entries"].docs) == journal_before
    assert db["journal_entries"].docs[0]["user_id"] == "usr_b"


@pytest.mark.asyncio
async def test_chat_context_is_a_single_profile_read(monkeypatch):
    db = _DB()
    db["users"].docs.append(_user())
    await consolidate_student_profile(db, "usr_a", now=NOW)
    db["student_psychological_profiles"].docs[0]["psychological_pattern_scores"]["stress"] = {
        "score": 7,
        "confidence": 0.5,
        "evidence_count": 4,
        "trend": "stable",
        "last_updated": NOW,
    }

    async def _deny(*_args, **_kwargs):
        raise AssertionError("chat context scanned a source collection")

    monkeypatch.setattr("services.patterns.adapters.collect_observations", _deny)
    context = await get_student_profile_context(db, "usr_a")
    assert "Meera" in context
    assert "7/10" in context
    assert "not a diagnosis" in context
    assert "Do not quote scores" in context
    prompt = format_system_prompt(student_profile_context=context)
    assert "STUDENT PROFILE" in prompt
    assert prompt.index("current statement") < prompt.index("STUDENT PROFILE")


@pytest.mark.asyncio
async def test_profile_routes_are_owner_scoped():
    db = _DB()
    db["users"].docs.append(_user())
    db["users"].docs.append(_user("usr_b", name="Arun"))
    from api.routes.memory import consolidate_memory, get_memory_profile

    body = await consolidate_memory(user_id="usr_a", db=db)
    assert body["success"] is True
    assert body["user_id"] == "usr_a"
    assert body["profile_updated"] is True
    assert "kept" in body
    viewed = await get_memory_profile(user_id="usr_a", db=db)
    assert viewed["user_id"] == "usr_a"
    assert viewed["identity"]["name"] == "Meera"
    assert "_id" not in viewed
    assert "edge_id" not in str(viewed)
    assert "Arun" not in str(viewed)
    with pytest.raises(HTTPException) as exc:
        await get_memory_profile(user_id="usr_b", db=db)
    assert exc.value.status_code == 404

    memory_route = (ROOT / "api" / "routes" / "memory.py").read_text(encoding="utf-8")
    assert "delete_student_profile" in memory_route
    assert "journal_entries" not in memory_route
    assert "sleep_logs" not in memory_route
    graph = (ROOT / "services" / "graph.py").read_text(encoding="utf-8")
    streaming = (ROOT / "services" / "streaming.py").read_text(encoding="utf-8")
    assert "student_profile_context" in graph
    assert "student_profile_context" in streaming

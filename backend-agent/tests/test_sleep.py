"""Sleep logs in context, patterns, reports, and meditation ranking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prompts import format_system_prompt
from services.meditation.engine import estimate_from_report, rank_sessions
from services.patterns.detect import detect_candidates
from services.patterns.score import apply_time_decay
from services.patterns.service import run_pattern_detection
from sleep.context import build_sleep_context, format_sleep_context
from sleep.patterns import (
    baseline_observation,
    duration_trend,
    meditation_support,
    mood_coincidence,
    task_coincidence,
)
from sleep.reader import (
    duration_from_times,
    get_recent_sleep,
    get_sleep_history,
    record_problem,
    sleep_cycle_date,
    valid_records,
)
from sleep.writer import create_sleep_log


def _night(user_id, date, minutes, bedtime="23:00", wake="07:00", created=None, **extra):
    return {
        "user_id": user_id,
        "date": date,
        "bedtime": bedtime,
        "wake_up_time": wake,
        "total_duration_minutes": minutes,
        "created_at": created or datetime.now(timezone.utc),
        **extra,
    }


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or datetime.min.replace(tzinfo=timezone.utc), reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._i]
        self._i += 1
        return doc


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [doc for doc in self.docs if _match(doc, query or {})]
        return found[0] if found else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return MagicMock()


def _match(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_match(doc, clause) for clause in value):
                return False
            continue
        if isinstance(value, dict):
            actual = doc.get(key)
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$gte" in value and (actual is None or actual < value["$gte"]):
                return False
            if "$ne" in value and actual == value["$ne"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


def _db():
    collections: Dict[str, _Collection] = {}

    class DB(dict):
        def __getitem__(self, name):
            collections.setdefault(name, _Collection())
            return collections[name]

    db = DB()
    db._c = collections
    return db


def _series():
    now = datetime.now(timezone.utc)
    rows = []
    for offset, minutes in enumerate((320, 330, 340, 450, 460, 470)):
        day = (now - timedelta(days=offset)).date().isoformat()
        rows.append(_night("user_a", day, minutes, created=now - timedelta(days=offset)))
    return rows


@pytest.mark.asyncio
async def test_recent_sleep_is_the_newest_created_row():
    db = _db()
    now = datetime.now(timezone.utc)
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    db["sleep_logs"].docs.extend(
        [
            _night("user_a", "2026-09-20", 400, created=now - timedelta(days=2)),
            _night("a@school.edu", "2026-09-22", 480, created=now),
        ]
    )
    doc = await get_recent_sleep(db, "user_a")
    assert doc["date"] == "2026-09-22"
    assert doc["total_duration_minutes"] == 480


@pytest.mark.asyncio
async def test_history_is_seven_days_by_created_at():
    db = _db()
    now = datetime.now(timezone.utc)
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    db["sleep_logs"].docs.extend(
        [
            _night("user_a", "2026-09-01", 400, created=now - timedelta(days=10)),
            _night("user_a", "2026-09-22", 420, created=now - timedelta(days=1)),
            _night("user_a", "2026-09-21", 430, created=now - timedelta(days=2)),
        ]
    )
    docs = await get_sleep_history(db, "user_a", days=7)
    assert [doc["date"] for doc in docs] == ["2026-09-22", "2026-09-21"]


@pytest.mark.asyncio
async def test_email_alias_matches_and_other_users_do_not():
    db = _db()
    now = datetime.now(timezone.utc)
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    db["sleep_logs"].docs.extend(
        [
            _night("a@school.edu", "2026-09-22", 480, created=now),
            _night("user_b", "2026-09-22", 480, created=now),
        ]
    )
    own = await get_recent_sleep(db, "user_a")
    other = await get_recent_sleep(db, "user_b")
    assert own["user_id"] == "a@school.edu"
    assert other["user_id"] == "user_b"
    history = await get_sleep_history(db, "user_a", days=7)
    assert all(doc["user_id"] != "user_b" for doc in history)


@pytest.mark.asyncio
async def test_cannot_create_for_someone_else():
    db = _db()
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    with pytest.raises(PermissionError):
        await create_sleep_log(
            db,
            "user_a",
            bedtime="23:00",
            wake_up_time="07:00",
            date="2026-09-22",
            claimed_user_id="user_b",
        )
    assert db["sleep_logs"].docs == []


def test_context_format_and_no_raw_dump():
    now = datetime.now(timezone.utc)
    rows = valid_records(
        [
            _night("user_a", "2026-09-22", 435, bedtime="11:30 PM", wake="6:45 AM", created=now, _id="abc123"),
            _night("user_a", "2026-09-21", 345, created=now - timedelta(days=1), _id="def456"),
        ]
    )
    text = format_sleep_context(rows)
    assert "RECENT SLEEP" in text
    assert "11:30 PM" in text
    assert "2026-09-21 → 5h 45m" in text
    assert "_id" not in text
    assert "abc123" not in text
    assert "ObjectId" not in text
    assert "do not mention sleep" in text.lower() or "do not mention" in text.lower()


def test_malformed_and_missing_rows_are_dropped():
    now = datetime.now(timezone.utc)
    future = (now + timedelta(days=3)).date().isoformat()
    rows = [
        _night("user_a", "2026-09-22", None, bedtime="nope", wake="later", created=now),
        _night("user_a", future, 400, created=now),
        {"bedtime": "23:00", "wake_up_time": "07:00", "date": "2026-09-20", "total_duration_minutes": 480},
        _night("user_a", "2026-09-19", 2000, created=now),
        _night("user_a", "2026-09-18", 400, created=None),
    ]
    rows[4]["created_at"] = None
    assert valid_records(rows) == []
    assert record_problem(rows[0]) == "missing_or_impossible_duration"
    assert record_problem(rows[1]) == "future_date"
    assert record_problem(rows[2]) == "missing_user_id"


def test_missing_context_and_failed_reader():
    assert format_sleep_context([]) == "No sleep data available"


@pytest.mark.asyncio
async def test_failed_reader_does_not_raise():
    db = _db()

    with patch("sleep.context.get_sleep_history", AsyncMock(side_effect=RuntimeError("down"))):
        text = await build_sleep_context(db, "user_a")
    assert text == "No sleep data available"


def test_personal_baseline_and_trend():
    rows = _series()
    text = baseline_observation(rows)
    assert text.startswith("[OBSERVATION]")
    assert "own" in text or "their own" in text
    assert "bad" not in text.lower()
    trend = duration_trend(rows)
    assert trend.startswith("[PATTERN]")
    assert "caused" not in trend.lower()


@pytest.mark.asyncio
async def test_create_derives_duration_for_the_authenticated_user():
    db = _db()
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    doc = await create_sleep_log(
        db,
        "user_a",
        bedtime="11:00 PM",
        wake_up_time="7:00 AM",
        date="2026-09-22",
    )
    assert doc["user_id"] == "user_a"
    assert doc["date"] == "2026-09-22"
    assert doc["total_duration_minutes"] == 480


def test_early_morning_bedtime_belongs_to_the_previous_night():
    assert sleep_cycle_date("2026-09-23", "1:30 AM") == "2026-09-22"
    assert sleep_cycle_date("2026-09-23", "11:30 PM") == "2026-09-23"


@pytest.mark.asyncio
async def test_create_rolls_a_morning_bedtime_back_one_night():
    db = _db()
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    doc = await create_sleep_log(
        db,
        "user_a",
        bedtime="1:30 AM",
        wake_up_time="8:00 AM",
        date="2026-09-23",
    )
    assert doc["date"] == "2026-09-22"
    assert doc["total_duration_minutes"] == 390


def test_overnight_duration_wraps_when_wake_is_earlier_on_the_clock():
    assert duration_from_times("11:00 PM", "7:15 AM") == 495
    assert duration_from_times("23:00", "07:15") == 495


def test_academic_and_meditation_overlaps_are_not_causes():
    from sleep.patterns import academic_coincidence, meditation_feedback

    rows = _series()
    low_days = [row["date"] for row in rows[:3]]
    marks = [{"date": day, "value": 40} for day in low_days]
    text = academic_coincidence(rows, marks)
    assert "coincided" in text
    assert "produced" in text
    feedback = meditation_feedback(
        rows,
        [{"user_helpfulness_feedback": "HELPFUL", "category": "sleep"}] * 2,
    )
    assert "explicitly" in feedback
    assert "changed their sleep" in feedback


def test_mood_and_task_patterns_and_contradiction():
    rows = _series()
    low_days = [row["date"] for row in rows[:3]]
    moods = [{"date": day, "value": "anxious"} for day in low_days]
    mood = mood_coincidence(rows, moods)
    assert mood["level"] == "pattern"
    assert "coincided" in mood["text"]
    assert "cause" in mood["text"]
    tasks = [{"date": day, "pending": 2} for day in low_days]
    assert "pending" in task_coincidence(rows, tasks)
    mixed = moods + [{"date": day, "value": "calm"} for day in low_days]
    assert mood_coincidence(rows, mixed)["level"] == "contradiction"
    observations = {
        "sleep": [
            {
                "value": row["total_duration_minutes"],
                "date": row["date"],
                "bedtime": row["bedtime"],
                "wake_up_time": row["wake_up_time"],
                "observed_at": row["created_at"],
            }
            for row in rows
        ],
        "mood": [
            {"at": datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc), "value": label}
            for day in low_days
            for label in ("anxious", "calm")
        ],
        "tasks": [],
        "academic": [],
        "sleep_practices": [],
    }
    stored = [
        item["description"]
        for item in detect_candidates("user_a", observations)
        if item.get("source_hint") == "sleep_logs"
    ]
    assert not any("more negative" in text for text in stored)


def test_sleep_candidates_distinguish_levels_and_decay():
    observations = {
        "sleep": [
            {
                "value": row["total_duration_minutes"],
                "date": row["date"],
                "bedtime": row["bedtime"],
                "wake_up_time": row["wake_up_time"],
                "observed_at": row["created_at"],
            }
            for row in _series()
        ],
        "mood": [],
        "tasks": [],
        "academic": [],
        "sleep_practices": [],
    }
    found = [
        item
        for item in detect_candidates("user_a", observations)
        if item.get("source_hint") == "sleep_logs"
    ]
    assert any(item["description"].startswith("[OBSERVATION]") for item in found)
    assert any(item["description"].startswith("[PATTERN]") for item in found)
    assert all("[HYPOTHESIS]" not in item["description"] for item in found)
    sample = found[0]
    decayed, inactive = apply_time_decay(
        sample["confidence"],
        sample["last_observed_at"] - timedelta(days=50),
    )
    assert decayed < sample["confidence"]
    assert inactive


@pytest.mark.asyncio
async def test_crisis_and_missing_consent_do_not_record_patterns():
    assert await run_pattern_detection(None, "user_a", message="I want to die", crisis=True) == 0
    with patch("services.patterns.service.personalization_enabled", AsyncMock(return_value=False)):
        assert await run_pattern_detection(MagicMock(), "user_a", message="I slept badly") == 0


@pytest.mark.asyncio
async def test_fetch_user_context_keeps_sleep_without_the_word_sleep(monkeypatch):
    from services.context import fetch_user_context

    db = _db()
    now = datetime.now(timezone.utc)
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    db["sleep_logs"].docs.append(_night("a@school.edu", now.date().isoformat(), 300, created=now))

    async def _no_marks(*_args, **_kwargs):
        return ""

    monkeypatch.setattr("services.context.academic_context_for_turn", _no_marks)
    monkeypatch.setattr(
        "services.patterns.get_pattern_context",
        AsyncMock(return_value="No longitudinal user patterns available for this turn."),
    )
    ctx = await fetch_user_context(db, "user_a", user_message="I'm having trouble concentrating.")
    assert "RECENT SLEEP" in ctx["sleep_context"]
    assert "5h 00m" in ctx["sleep_context"]
    prompt = format_system_prompt(sleep_context=ctx["sleep_context"])
    assert "RECENT SLEEP" in prompt
    assert "Mention it only when" in prompt


def test_send_and_stream_both_pass_sleep_context():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    graph = (root / "services" / "graph.py").read_text(encoding="utf-8")
    streaming = (root / "services" / "streaming.py").read_text(encoding="utf-8")
    assert 'sleep_context=context.get("sleep_context")' in graph
    assert 'sleep_context=user_context.get("sleep_context")' in streaming


def test_unrelated_topic_keeps_sleep_available_but_says_not_to_mention_it():
    prompt = format_system_prompt(
        sleep_context=format_sleep_context(_series()),
    )
    assert "RECENT SLEEP" in prompt
    assert "friend" in prompt.lower()
    assert "leave sleep out" in prompt.lower()


def test_meditation_hint_does_not_map_poor_sleep_to_a_sleep_practice():
    rows = _series()
    calm = meditation_support(rows, "CALM", "evening")
    assert calm["relevant"] is False
    fatigue = meditation_support(rows, "COGNITIVE_FATIGUE", "afternoon")
    assert fatigue["prefer_low_effort"] is True
    assert fatigue["prefer_sleep"] is False
    evening = meditation_support(rows, "SLEEP_PREPARATION", "evening")
    assert evening["prefer_sleep"] is True

    estimate = estimate_from_report(
        valence=-0.2,
        arousal=-0.2,
        dominance=0.1,
        confidence=0.8,
        latent_states=[{"state": "SLEEP_PREPARATION", "probability": 0.8}],
    )
    from meditation.data import get_all_sessions

    sleep_ids = {
        session["id"]
        for session in get_all_sessions()
        if "SLEEP_PREPARATION" in (session.get("target_latent_states") or [])
    }
    plain = {item.meditation_id: item.final_score for item in rank_sessions(estimate)}
    boosted = {
        item.meditation_id: item.final_score
        for item in rank_sessions(estimate, sleep_support=evening)
    }
    assert boosted["324"] > plain["324"]
    for meditation_id, score in plain.items():
        if meditation_id in sleep_ids:
            continue
        assert boosted[meditation_id] == score


@pytest.mark.asyncio
async def test_report_prompt_receives_sleep_context():
    from services.session_report import generate_session_report

    seen = {}

    async def _invoke(messages):
        seen["text"] = messages[1].content
        return MagicMock(
            content=(
                '{"summary": "The afternoon stayed fairly steady.",'
                '"valence": 0.2, "arousal": -0.2, "dominance": 0.1, "confidence": 0.8,'
                '"latent_states": [{"state": "CALM", "probability": 0.7}],'
                '"crisis_signal": false}'
            )
        )

    llm = MagicMock()
    llm.ainvoke = _invoke

    async def _load(*_args, **_kwargs):
        return [{"role": "user", "content": "I felt okay today.", "message_kind": "chat"}]

    with patch("services.session_report.load_session_messages", _load), patch(
        "llm_provider.get_llm", return_value=llm
    ), patch(
        "sleep.context.build_sleep_context",
        AsyncMock(return_value="RECENT SLEEP\nLast night:\n- Duration: 5h 20m"),
    ), patch(
        "services.patterns.retrieve.retrieve_relevant_patterns",
        AsyncMock(
            return_value=[
                {
                    "domains": ["sleep"],
                    "description": "[PATTERN] Shorter sleep has repeatedly coincided with harder focus.",
                    "confidence": 0.8,
                }
            ]
        ),
    ):
        await generate_session_report(_db(), user_id="user_a", session_id="sess")

    assert "RECENT SLEEP" in seen["text"]
    assert "5h 20m" in seen["text"]
    assert "[PATTERN]" in seen["text"]
    assert '"_id"' not in seen["text"]

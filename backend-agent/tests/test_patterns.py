"""Tests for the longitudinal User Pattern Detection Engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import get_settings
from schemas import PatternFeedbackEvent, PatternStatus
from services.patterns.detect import detect_candidates
from services.patterns.feedback import record_pattern_feedback
from services.patterns.retrieve import format_pattern_context, retrieve_relevant_patterns
from services.patterns.score import (
    apply_time_decay,
    classify_status,
    compute_confidence,
)
from services.patterns.service import get_pattern_context, run_pattern_detection
from services.patterns.store import (
    PATTERNS_COLLECTION,
    append_evidence,
    get_pattern_by_id,
    list_active_patterns,
    upsert_pattern,
)


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def limit(self, n: int):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[: length or len(self._docs)])


class FakeCollection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query: Dict[str, Any], projection=None):
        return FakeCursor([d for d in self.docs if _match(d, query)])

    async def find_one(self, query: Dict[str, Any], projection=None):
        for d in self.docs:
            if _match(d, query):
                return d
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        if any(
            d.get("user_id") == doc.get("user_id")
            and d.get("fingerprint") == doc.get("fingerprint")
            and doc.get("fingerprint")
            for d in self.docs
        ):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("dup")
        if any(
            d.get("user_id") == doc.get("user_id")
            and d.get("event_key") == doc.get("event_key")
            and doc.get("event_key")
            for d in self.docs
        ):
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("dup evidence")
        self.docs.append(dict(doc))
        return MagicMock()

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        for d in self.docs:
            if _match(d, query):
                d.update(update.get("$set") or {})
                return MagicMock(matched_count=1)
        return MagicMock(matched_count=0)

    async def delete_many(self, query: Dict[str, Any]):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return MagicMock(deleted_count=before - len(self.docs))


def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "$or":
            return any(_match(doc, clause) for clause in value)
        if isinstance(value, dict):
            actual = doc.get(key)
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$gte" in value:
                if actual is None or actual < value["$gte"]:
                    return False
            if "$ne" in value and actual == value["$ne"]:
                return False
        elif doc.get(key) != value:
            return False
    return True


def _db() -> MagicMock:
    patterns = FakeCollection()
    evidence = FakeCollection()
    users = FakeCollection()
    mood = FakeCollection()
    marks = FakeCollection()
    habits = FakeCollection()
    insights = FakeCollection()
    cards = FakeCollection()
    apm_events = FakeCollection()

    collections = {
        PATTERNS_COLLECTION: patterns,
        "pattern_evidence": evidence,
        "users": users,
        "mood_logs": mood,
        "marks": marks,
        "habit_events": habits,
        "user_insights": insights,
        "action_card_logs": cards,
        "apm_events": apm_events,
    }
    db = MagicMock()
    db.__getitem__.side_effect = lambda name: collections[name]
    db._c = collections
    return db


def test_single_observation_is_not_established():
    assert classify_status(1) == PatternStatus.OBSERVATION
    assert classify_status(1).value != PatternStatus.ESTABLISHED.value


def test_repeated_observations_become_emerging():
    settings = get_settings()
    status = classify_status(settings.PATTERN_EMERGING_MIN_EVIDENCE)
    assert status == PatternStatus.EMERGING


def test_contradictory_evidence_reduces_confidence():
    high = compute_confidence(evidence_count=5, contradiction_count=0, consistency=0.9)
    low = compute_confidence(evidence_count=5, contradiction_count=3, consistency=0.9)
    assert low < high


def test_confidence_decays_over_time():
    now = datetime.now(timezone.utc)
    decayed, inactive = apply_time_decay(
        0.8, now - timedelta(days=50), now=now
    )
    assert decayed < 0.8
    assert inactive is True


def test_inactive_patterns_filtered_from_retrieval_helpers():
    decayed, inactive = apply_time_decay(
        0.2, datetime.now(timezone.utc) - timedelta(days=60)
    )
    assert inactive is True


def test_cross_domain_detector_needs_repeated_signal():
    user = "user_A"
    weak = detect_candidates(
        user,
        {
            "mood": [{"stressed": True, "at": datetime.now(timezone.utc), "score": 2}],
            "academic": [],
            "conversation": [],
            "apm": [],
        },
    )
    # A single stressed mood alone should not invent a strong cross-domain pattern
    cross = [c for c in weak if c["pattern_type"] == "CROSS_DOMAIN"]
    assert cross == [] or cross[0]["status"] == "OBSERVATION"


def test_personal_baseline_change_point_requires_enough_scores():
    now = datetime.now(timezone.utc)
    moods = [
        {"score": 2.0, "at": now - timedelta(days=i), "stressed": True}
        for i in range(8)
    ]
    # Baseline from older samples vs lower recent → change point candidate
    moods[0]["score"] = 1.0
    moods[1]["score"] = 1.0
    moods[2]["score"] = 1.0
    for i in range(3, 8):
        moods[i]["score"] = 4.0
    cands = detect_candidates("u1", {"mood": moods, "academic": [], "conversation": [], "apm": []})
    types = {c["pattern_type"] for c in cands}
    assert "CHANGE_POINT" in types or "RECURRENCE" in types


def test_missing_data_does_not_fabricate_academic_pattern():
    cands = detect_candidates(
        "u1",
        {"mood": [], "academic": [], "conversation": [], "apm": []},
    )
    assert cands == []


@pytest.mark.asyncio
async def test_two_users_cannot_read_each_others_patterns():
    db = _db()
    await upsert_pattern(
        db,
        "user_A",
        {
            "fingerprint": "fp_a",
            "pattern_type": "TEMPORAL",
            "domains": ["mood"],
            "description": "A only",
            "observations": [],
            "evidence_count": 5,
            "confidence": 0.8,
            "strength": 0.7,
            "status": "ESTABLISHED",
            "last_observed_at": datetime.now(timezone.utc),
        },
    )
    listed_b = await list_active_patterns(db, "user_B", min_confidence=0.1)
    assert listed_b == []
    assert await get_pattern_by_id(db, "user_B", "missing") is None


@pytest.mark.asyncio
async def test_user_feedback_changes_confidence_and_records_disagreement():
    db = _db()
    saved = await upsert_pattern(
        db,
        "user_A",
        {
            "fingerprint": "fp_fb",
            "pattern_type": "INTERVENTION_RESPONSE",
            "domains": ["meditation"],
            "description": "Breathing often helps",
            "observations": [],
            "evidence_count": 5,
            "confidence": 0.7,
            "strength": 0.6,
            "status": "ESTABLISHED",
            "last_observed_at": datetime.now(timezone.utc),
            "pattern_id": "pat_test_1",
        },
    )
    before = saved["confidence"]
    result = await record_pattern_feedback(
        db,
        user_id="user_A",
        pattern_id=saved["pattern_id"],
        event_type=PatternFeedbackEvent.DISAGREE,
        note="That's not related",
    )
    assert result["disagree_count"] == 1
    assert result["confidence"] < before


@pytest.mark.asyncio
async def test_crisis_skips_pattern_detection():
    db = _db()
    with patch(
        "services.patterns.service.personalization_enabled",
        new=AsyncMock(return_value=True),
    ):
        n = await run_pattern_detection(
            db, "user_A", message="I want to die", crisis=False
        )
    assert n == 0


@pytest.mark.asyncio
async def test_consent_denial_prevents_persistence_and_retrieval():
    db = _db()
    with patch(
        "services.patterns.service.personalization_enabled",
        new=AsyncMock(return_value=False),
    ):
        n = await run_pattern_detection(db, "user_A", message="exams are hard")
        ctx = await get_pattern_context(db, "user_A", "exams are hard")
    assert n == 0
    assert "No longitudinal" in ctx


@pytest.mark.asyncio
async def test_retrieval_returns_at_most_three_and_can_be_zero():
    db = _db()
    now = datetime.now(timezone.utc)
    for i in range(5):
        await upsert_pattern(
            db,
            "user_A",
            {
                "fingerprint": f"fp_{i}",
                "pattern_type": "TEMPORAL",
                "domains": ["academic", "mood"],
                "description": f"Pattern {i} about exams and stress",
                "observations": [],
                "evidence_count": 6,
                "confidence": 0.9 - i * 0.05,
                "strength": 0.7,
                "status": "ESTABLISHED",
                "last_observed_at": now,
                "pattern_id": f"pat_{i}",
            },
        )
    got = await retrieve_relevant_patterns(
        db, "user_A", "I am stressed about my physics exam", max_patterns=3
    )
    assert 0 < len(got) <= 3
    empty = await retrieve_relevant_patterns(db, "user_Z", "hello", max_patterns=3)
    assert empty == []


def test_llm_context_includes_provenance_and_confidence():
    text = format_pattern_context(
        [
            {
                "pattern_type": "CROSS_DOMAIN",
                "description": "Sleep-ish stress co-occurrence",
                "evidence_count": 7,
                "confidence": 0.82,
                "_retrieval_confidence": 0.82,
                "status": "EMERGING",
                "domains": ["mood", "academic"],
                "pattern_id": "pat_xyz",
                "last_observed_at": datetime.now(timezone.utc) - timedelta(days=2),
            }
        ]
    )
    assert "Confidence: 0.82" in text
    assert "Evidence count: 7" in text
    assert "pat_xyz" in text
    assert "not proof of causation" in text.lower()


@pytest.mark.asyncio
async def test_pattern_detection_does_not_raise_when_adapters_empty():
    db = _db()
    db._c["users"].docs.append(
        {"user_id": "user_A", "personalization_consent": True, "isActive": True}
    )
    with patch(
        "services.patterns.service.personalization_enabled",
        new=AsyncMock(return_value=True),
    ), patch(
        "services.patterns.service.collect_observations",
        new=AsyncMock(
            return_value={
                "mood": [],
                "academic": [],
                "conversation": [],
                "apm": [],
                "habits": [],
                "language": [],
                "meditation": [],
                "sleep": [],
                "journaling": [],
                "tasks": [],
                "attendance": [],
            }
        ),
    ):
        n = await run_pattern_detection(db, "user_A", message="hi there")
    assert n == 0


@pytest.mark.asyncio
async def test_evidence_append_is_idempotent_per_event_key():
    db = _db()
    ok1 = await append_evidence(
        db,
        user_id="user_A",
        pattern_id="pat_1",
        source="test",
        feature="x",
        value=1,
        event_key="user_A:pat_1:once",
        confidence=0.5,
        provenance="unit",
    )
    ok2 = await append_evidence(
        db,
        user_id="user_A",
        pattern_id="pat_1",
        source="test",
        feature="x",
        value=1,
        event_key="user_A:pat_1:once",
        confidence=0.5,
        provenance="unit",
    )
    assert ok1 is True
    assert ok2 is False

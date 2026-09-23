"""Journal creation, privacy, context, and pattern input."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from journaling.context import build_journal_context, format_journal_context
from journaling.models import validate_entry
from journaling.patterns import findings, sleep_coincidence
from journaling.service import create_journal_entry, get_entry, recent_entries
from prompts import format_system_prompt
from services.meditation.engine import estimate_from_report
from services.patterns.detect import detect_candidates
from services.patterns.score import apply_time_decay
from services.patterns.service import run_pattern_detection


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        doc = self._docs[self._i]
        self._i += 1
        return doc

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        docs = list(self._docs)
        return docs if length is None else docs[:length]


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [doc for doc in self.docs if _match(doc, query or {})]
        return found[0] if found else None

    async def insert_one(self, doc):
        self.docs.append(doc)
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
        elif doc.get(key) != value:
            return False
    return True


def _db():
    collections: Dict[str, _Collection] = {}

    class DB(dict):
        def __getitem__(self, name):
            collections.setdefault(name, _Collection())
            return collections[name]

    return DB()


def _user(db, user_id="user_a", email="a@school.edu"):
    db["users"].docs.append({"user_id": user_id, "email": email})


@pytest.mark.asyncio
async def test_create_validates_and_keeps_optional_tags():
    db = _db()
    _user(db)
    with pytest.raises(ValueError):
        await create_journal_entry(db, "user_a", title="No", content="long enough content", mood="😐")
    with pytest.raises(ValueError):
        await create_journal_entry(db, "user_a", title="A real title", content="short", mood="😐")
    with pytest.raises(ValueError):
        await create_journal_entry(db, "user_a", title="A real title", content="long enough content", mood="angry")
    doc = await create_journal_entry(
        db,
        "user_a",
        title="Rough week",
        content="Today was overwhelming and loud.",
        mood="😐",
        tags=["#calm", "work"],
        time_spent=30,
    )
    assert doc["mood"] == "😐"
    assert doc["tags"] == ["#calm", "work"]
    assert doc["time_spent"] == 30
    assert doc["content"] == "Today was overwhelming and loud."
    validate_entry(title="Okay", content="1234567890", mood="😊", time_spent=0)


@pytest.mark.asyncio
async def test_duplicate_submit_returns_the_same_entry():
    db = _db()
    _user(db)
    first = await create_journal_entry(
        db, "user_a", title="Rough week", content="Today was overwhelming and loud.", mood="😐"
    )
    second = await create_journal_entry(
        db, "user_a", title="Rough week", content="Today was overwhelming and loud.", mood="😐"
    )
    assert second["duplicate"] is True
    assert second["_id"] == first["_id"]
    assert len(db["journal_entries"].docs) == 1


@pytest.mark.asyncio
async def test_users_are_isolated_for_read_and_write():
    db = _db()
    _user(db, "user_a", "a@school.edu")
    _user(db, "user_b", "b@school.edu")
    with pytest.raises(PermissionError):
        await create_journal_entry(
            db,
            "user_a",
            title="Rough week",
            content="Today was overwhelming and loud.",
            mood="😢",
            claimed_user_id="user_b",
        )
    own = await create_journal_entry(
        db, "user_a", title="Rough week", content="Today was overwhelming and loud.", mood="😢"
    )
    other = await create_journal_entry(
        db, "user_b", title="Rough week", content="Today was overwhelming and loud.", mood="😢"
    )
    assert own["user_id"] == "user_a"
    assert other["user_id"] == "user_b"
    assert await get_entry(db, "user_b", str(own["_id"])) is None
    visible = await recent_entries(db, "user_a")
    assert [row["user_id"] for row in visible] == ["user_a"]


@pytest.mark.asyncio
async def test_recent_entries_follow_timestamp_not_insert_order():
    db = _db()
    _user(db)
    now = datetime.now(timezone.utc)
    db["journal_entries"].docs.extend(
        [
            {
                "_id": "old",
                "user_id": "user_a",
                "mood": "😃",
                "title": "Earlier",
                "content": "This was written earlier today.",
                "tags": [],
                "timestamp": now - timedelta(days=2),
                "created_at": now - timedelta(days=2),
                "updated_at": now - timedelta(days=2),
            },
            {
                "_id": "new",
                "user_id": "user_a",
                "mood": "😢",
                "title": "Later",
                "content": "This was written more recently.",
                "tags": [],
                "timestamp": now,
                "created_at": now,
                "updated_at": now,
            },
        ]
    )
    rows = await recent_entries(db, "user_a", limit=5)
    assert [row["title"] for row in rows] == ["Later", "Earlier"]


def test_context_truncates_and_hides_storage_ids():
    now = datetime.now(timezone.utc)
    long_body = "Today was overwhelming. " * 40
    text = format_journal_context(
        [
            {
                "_id": "abc123",
                "mood": "😐",
                "title": "Rough week",
                "content": long_body,
                "timestamp": now,
            }
        ]
    )
    assert "RECENT JOURNAL CONTEXT" in text
    assert "Rough week" in text
    assert "abc123" not in text
    assert long_body not in text
    assert len(text) <= 1200

    class Tiny:
        JOURNAL_CONTEXT_LIMIT = 5
        JOURNAL_PREVIEW_CHARS = 30
        JOURNAL_CONTEXT_MAX_CHARS = 90

    with patch("journaling.context.get_settings", return_value=Tiny()):
        clipped = format_journal_context(
            [
                {
                    "mood": "😢",
                    "title": "Difficult exam",
                    "content": "I felt disappointed after the paper and could not settle.",
                    "timestamp": now,
                }
            ]
        )
    assert len(clipped) <= 91
    assert clipped.endswith("…")


def test_missing_journal_context_and_failed_reader():
    assert format_journal_context([]) == "No journal entries available."


@pytest.mark.asyncio
async def test_failed_reader_does_not_raise():
    with patch("journaling.context.recent_entries", AsyncMock(side_effect=RuntimeError("down"))):
        assert await build_journal_context(_db(), "user_a") == "No journal entries available."


def test_repeated_journal_patterns_and_contradiction():
    now = datetime.now(timezone.utc)
    entries = [
        {
            "mood": "😢",
            "date": f"2026-09-{day:02d}",
            "topics": ["exam"],
            "title": "Exam",
            "observed_at": now,
        }
        for day in range(1, 5)
    ]
    found = findings(entries)
    assert any(item["text"].startswith("[OBSERVATION]") for item in found)
    assert any("exam" in item["text"] and item["level"] == "pattern" for item in found)
    sleep_rows = [
        {"date": "2026-09-01", "value": 280},
        {"date": "2026-09-02", "value": 290},
        {"date": "2026-09-03", "value": 300},
        {"date": "2026-09-04", "value": 500},
        {"date": "2026-09-05", "value": 510},
        {"date": "2026-09-06", "value": 520},
    ]
    overlap = sleep_coincidence(entries, sleep_rows)
    assert overlap["level"] == "pattern"
    mixed = entries + [
        {"mood": "😊", "date": f"2026-09-{day:02d}", "topics": [], "title": "Ok", "observed_at": now}
        for day in range(1, 4)
    ]
    assert sleep_coincidence(mixed, sleep_rows)["level"] == "contradiction"
    stored = [
        item["description"]
        for item in detect_candidates(
            "user_a",
            {
                "journaling": mixed,
                "sleep": sleep_rows,
                "tasks": [{"date": f"2026-09-{day:02d}", "pending": 2} for day in range(1, 4)],
                "academic": [{"date": f"2026-09-{day:02d}", "value": 40} for day in range(1, 4)],
                "meditation_feedback": [],
            },
        )
        if item.get("source_hint") == "journal_entries"
    ]
    assert any("pending" in text for text in stored)
    assert any("marks" in text for text in stored)
    assert not any("caused" in text.lower() for text in stored)
    sample = next(item for item in detect_candidates("user_a", {"journaling": entries}) if item.get("source_hint") == "journal_entries")
    decayed, inactive = apply_time_decay(sample["confidence"], datetime.now(timezone.utc) - timedelta(days=50))
    assert decayed < sample["confidence"]
    assert inactive


@pytest.mark.asyncio
async def test_crisis_entries_are_not_pattern_fuel_and_consent_still_gates():
    db = _db()
    _user(db)
    await create_journal_entry(
        db,
        "user_a",
        title="Hard night",
        content="I want to die and I do not know what to do.",
        mood="😢",
    )
    from journaling.service import entries_for_patterns

    assert await entries_for_patterns(db, "user_a") == []
    assert await run_pattern_detection(None, "user_a", message="I want to die", crisis=True) == 0
    with patch("services.patterns.service.personalization_enabled", AsyncMock(return_value=False)):
        assert await run_pattern_detection(MagicMock(), "user_a", message="I wrote in my journal") == 0


@pytest.mark.asyncio
async def test_pattern_failure_does_not_block_creation():
    db = _db()
    _user(db)
    with patch("services.patterns.detect.detect_candidates", side_effect=RuntimeError("pattern down")):
        doc = await create_journal_entry(
            db,
            "user_a",
            title="Still here",
            content="I wanted to write this down anyway.",
            mood="😃",
        )
    assert doc["duplicate"] is False
    assert len(db["journal_entries"].docs) == 1


@pytest.mark.asyncio
async def test_chat_context_includes_journal_without_requiring_the_word(monkeypatch):
    from services.context import fetch_user_context

    db = _db()
    _user(db)
    await create_journal_entry(
        db,
        "user_a",
        title="Physics test",
        content="I could not focus after the physics test today.",
        mood="😢",
    )

    async def _no_marks(*_args, **_kwargs):
        return ""

    monkeypatch.setattr("services.context.academic_context_for_turn", _no_marks)
    monkeypatch.setattr(
        "services.patterns.get_pattern_context",
        AsyncMock(return_value="No longitudinal user patterns available for this turn."),
    )
    ctx = await fetch_user_context(db, "user_a", user_message="I can't stop thinking about that exam.")
    assert "Physics test" in ctx["journal_context"]
    assert "RECENT JOURNAL CONTEXT" in ctx["journal_context"]
    prompt = format_system_prompt(journal_context=ctx["journal_context"])
    assert "Physics test" in prompt
    assert "Do not say you searched a database" in prompt


def test_send_and_stream_pass_journal_context():
    root = Path(__file__).resolve().parents[1]
    graph = (root / "services" / "graph.py").read_text(encoding="utf-8")
    streaming = (root / "services" / "streaming.py").read_text(encoding="utf-8")
    app = (root / "streamlit_app.py").read_text(encoding="utf-8")
    assert 'journal_context=context.get("journal_context")' in graph
    assert 'journal_context=user_context.get("journal_context")' in streaming
    assert "/journal/entry" in app
    assert "New Journal" in app
    assert "journal_entries" not in app


def test_journal_pattern_cannot_outrank_the_report_state():
    estimate = estimate_from_report(
        valence=-0.2,
        arousal=0.4,
        dominance=0.0,
        confidence=0.8,
        latent_states=[{"state": "ANXIETY_HIGH", "probability": 0.8}],
        patterns=[{"domains": ["journaling"], "confidence": 0.9}],
    )
    assert estimate.top_state == "ANXIETY_HIGH"
    low = next(item for item in estimate.latent if item.state == "LOW_MOOD")
    assert low.probability < 0.8


@pytest.mark.asyncio
async def test_report_receives_journal_preview():
    from services.session_report import generate_session_report

    seen = {}

    async def _invoke(messages):
        seen["text"] = messages[1].content
        return MagicMock(
            content=(
                '{"summary": "The exam was still on their mind.",'
                '"valence": -0.3, "arousal": 0.2, "dominance": 0.0, "confidence": 0.8,'
                '"latent_states": [{"state": "ANXIETY_HIGH", "probability": 0.7}],'
                '"crisis_signal": false}'
            )
        )

    llm = MagicMock()
    llm.ainvoke = _invoke

    async def _load(*_args, **_kwargs):
        return [{"role": "user", "content": "I can't stop thinking about that exam.", "message_kind": "chat"}]

    with patch("services.session_report.load_session_messages", _load), patch(
        "llm_provider.get_llm", return_value=llm
    ), patch(
        "journaling.context.build_journal_context",
        AsyncMock(return_value='RECENT JOURNAL CONTEXT\n  Title: Physics test\n  Summary/content preview: "I could not focus"'),
    ), patch(
        "services.patterns.retrieve.retrieve_relevant_patterns",
        AsyncMock(
            return_value=[
                {
                    "domains": ["journaling"],
                    "description": "[PATTERN] exam shows up in 3 recent journal entries.",
                    "confidence": 0.8,
                }
            ]
        ),
    ):
        await generate_session_report(_db(), user_id="user_a", session_id="sess")

    assert "Physics test" in seen["text"]
    assert "[PATTERN]" in seen["text"]
    assert '"_id"' not in seen["text"]

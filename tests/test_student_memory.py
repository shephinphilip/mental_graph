"""Facts are extracted from reports, confirmed rather than duplicated, and fade."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from student_memory.context import format_memory_context
from student_memory.models import normalize_facts
from student_memory.store import (
    consolidate_student_memory,
    effective_importance,
    retrieve_facts,
    upsert_facts,
)

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d.get(key) or 0, reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return _Cursor([d for d in self.docs if _match(d, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [d for d in self.docs if _match(d, query or {})]
        return found[0] if found else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace()

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _match(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def delete_many(self, query):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not _match(d, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))


def _match(doc, query):
    for key, value in query.items():
        if isinstance(value, dict):
            if "$ne" in value and doc.get(key) == value["$ne"]:
                return False
            continue
        if doc.get(key) != value:
            return False
    return True


def _db():
    cols: Dict[str, _Collection] = {}

    class DB(dict):
        def __getitem__(self, name):
            cols.setdefault(name, _Collection())
            return cols[name]

    db = DB()
    db["users"].docs.append({"user_id": "stu_a", "personalization_consent": True})
    return db


def test_normalize_drops_quotes_diagnoses_and_duplicates():
    facts = normalize_facts(
        [
            {"fact": "Board exams are in March", "category": "ACADEMIC", "importance": 0.8},
            {"fact": "board exams are in march", "category": "ACADEMIC"},
            {"fact": "Has clinical depression", "category": "HEALTH"},
            {"fact": "ok", "category": "OTHER"},
            "Lives with grandparents on weekdays",
            {"fact": "Walking helps when thoughts race", "category": "NONSENSE", "importance": 9},
        ]
    )
    texts = [f["fact"] for f in facts]
    assert texts == [
        "Board exams are in March",
        "Lives with grandparents on weekdays",
        "Walking helps when thoughts race",
    ]
    assert facts[2]["category"] == "OTHER"
    assert facts[2]["importance"] == 1.0


@pytest.mark.asyncio
async def test_repeated_fact_is_confirmed_not_duplicated():
    db = _db()
    first = await upsert_facts(
        db, "stu_a", "s1", [{"fact": "Board exams are in March", "category": "ACADEMIC", "importance": 0.6}], now=NOW
    )
    second = await upsert_facts(
        db, "stu_a", "s2", [{"fact": "Board exams are in March.", "category": "ACADEMIC", "importance": 0.5}],
        now=NOW + timedelta(days=1),
    )
    assert first == {"inserted": 1, "confirmed": 0}
    assert second == {"inserted": 0, "confirmed": 1}
    rows = db["student_memories"].docs
    assert len(rows) == 1
    assert rows[0]["source_sessions"] == ["s1", "s2"]
    assert rows[0]["importance"] > 0.6


@pytest.mark.asyncio
async def test_importance_decays_and_faint_facts_leave_the_prompt():
    db = _db()
    await upsert_facts(
        db,
        "stu_a",
        "s1",
        [
            {"fact": "Board exams are in March", "category": "ACADEMIC", "importance": 0.9},
            {"fact": "Skipped lunch once last week", "category": "HEALTH", "importance": 0.25},
        ],
        now=NOW,
    )
    fresh = await retrieve_facts(db, "stu_a", now=NOW)
    assert [f["fact"] for f in fresh] == ["Board exams are in March", "Skipped lunch once last week"]
    later = await retrieve_facts(db, "stu_a", now=NOW + timedelta(days=20))
    assert [f["fact"] for f in later] == ["Board exams are in March"]
    row = db["student_memories"].docs[0]
    assert effective_importance(row, now=NOW + timedelta(days=10)) == pytest.approx(0.8, abs=0.01)
    text = format_memory_context(later)
    assert "[ACADEMIC] Board exams are in March (strong)" in text
    assert "confirm before building on a faint one" in text


@pytest.mark.asyncio
async def test_consolidation_rewrites_profile_and_archives_faded_facts():
    db = _db()
    await upsert_facts(
        db,
        "stu_a",
        "s1",
        [
            {"fact": "Board exams are in March", "category": "ACADEMIC", "importance": 0.9},
            {"fact": "Walking helps when thoughts race", "category": "COPING", "importance": 0.7},
            {"fact": "Skipped lunch once last week", "category": "HEALTH", "importance": 0.25},
        ],
        now=NOW,
    )
    result = await consolidate_student_memory(db, "stu_a", now=NOW + timedelta(days=20))
    assert result["kept"] == 2
    assert result["archived"] == 1
    user = db["users"].docs[0]
    assert "Academic: Board exams are in March." in user["memory_summary"]
    assert "Coping: Walking helps when thoughts race." in user["memory_summary"]
    assert user["key_takeaways"][0] == "Board exams are in March"
    archived = [d for d in db["student_memories"].docs if d.get("archived")]
    assert [d["fact"] for d in archived] == ["Skipped lunch once last week"]


@pytest.mark.asyncio
async def test_facts_are_isolated_per_user():
    db = _db()
    await upsert_facts(db, "stu_a", "s1", [{"fact": "Board exams are in March", "category": "ACADEMIC"}], now=NOW)
    assert await retrieve_facts(db, "stu_b", now=NOW) == []

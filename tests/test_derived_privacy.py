"""Derived copies must not keep sealed plaintext or ciphertext fragments."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.erasure import start_erasure
from services.mongo_graph import generate_node_id, sanitize_relationship_properties, upsert_relationship
from services.security import encrypt_payload
from services.student_profile import assemble_profile
from student_memory.store import delete_student_memory, upsert_facts
from tests.test_student_memory import NOW, _db
from tests.test_student_profile import _user


def _walk_enc(value) -> list[str]:
    found = []
    if isinstance(value, str) and "enc::" in value:
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_walk_enc(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_walk_enc(item))
    return found


@pytest.mark.asyncio
async def test_erasure_removes_derived_user_memory_copies_and_keeps_other_user():
    db = _db()
    db["users"].docs[0]["memory_summary"] = "secret-summary-a"
    db["users"].docs[0]["key_takeaways"] = ["secret-takeaway-a"]
    db["users"].docs.append(
        {
            "user_id": "stu_b",
            "personalization_consent": True,
            "memory_summary": "secret-summary-b",
            "key_takeaways": ["secret-takeaway-b"],
        }
    )
    await upsert_facts(
        db,
        "stu_a",
        "s1",
        [{"fact": "Board exams are in March", "category": "ACADEMIC", "importance": 0.9}],
        now=NOW,
    )
    result = await start_erasure(db, "stu_a")
    assert result["status"] == "succeeded"
    assert all(doc.get("user_id") != "stu_a" for doc in db["student_memories"].docs)
    owner = next(doc for doc in db["users"].docs if doc["user_id"] == "stu_a")
    other = next(doc for doc in db["users"].docs if doc["user_id"] == "stu_b")
    assert "memory_summary" not in owner
    assert "key_takeaways" not in owner
    assert other["memory_summary"] == "secret-summary-b"
    assert other["key_takeaways"] == ["secret-takeaway-b"]


@pytest.mark.asyncio
async def test_delete_student_memory_unsets_user_copies_without_touching_others():
    db = _db()
    db["users"].docs[0]["memory_summary"] = "secret-summary-a"
    db["users"].docs.append({"user_id": "stu_b", "memory_summary": "secret-summary-b"})
    await upsert_facts(
        db,
        "stu_a",
        "s1",
        [{"fact": "Board exams are in March", "category": "ACADEMIC", "importance": 0.9}],
        now=NOW,
    )
    deleted = await delete_student_memory(db, "stu_a")
    assert deleted == 1
    assert db["student_memories"].docs == []
    assert "memory_summary" not in db["users"].docs[0]
    assert db["users"].docs[1]["memory_summary"] == "secret-summary-b"


def test_profile_does_not_copy_sealed_report_prose_or_ciphertext():
    sealed = encrypt_payload("I cannot stop thinking about that exam.")
    profile = assemble_profile(
        _user(),
        {},
        [
            {
                "session_id": "s1",
                "created_at": datetime(2026, 9, 27, tzinfo=timezone.utc),
                "psychiatric_summary": sealed,
                "summary": sealed,
                "crisis_signal": False,
                "events": [{"label": "Physics mock", "resolved": False}],
                "proposed_tasks": [{"title": "Open the worksheet"}],
            }
        ],
        [],
        personalization=True,
        now=datetime(2026, 9, 27, tzinfo=timezone.utc),
    )
    snapshot = profile["conversations"]["recent_sessions"][0]
    assert snapshot["main_concern"] == "Physics mock"
    assert sealed not in str(profile)
    assert _walk_enc(profile) == []
    assert "I cannot stop thinking" not in str(profile)


def test_relationship_properties_keep_structured_metadata_only():
    cleaned = sanitize_relationship_properties(
        {
            "intensity": 8,
            "source_session_id": "sess_1",
            "note": "I feel anxious about the exam and told my mother.",
            "quote": "I want to give up",
            "journal": "Today was overwhelming and loud.",
            "status": "active",
        }
    )
    assert cleaned == {
        "intensity": 8,
        "source_session_id": "sess_1",
        "status": "active",
    }
    assert "anxious" not in str(cleaned)
    assert sanitize_relationship_properties({"status": "enc::token", "intensity": 1}) == {
        "intensity": 1
    }


@pytest.mark.asyncio
async def test_relationship_write_drops_freeform_properties():
    captured = {}

    class _Rels:
        async def update_one(self, query, update, upsert=False):
            captured["properties"] = (update.get("$set") or {}).get("properties")

    class _DB(dict):
        def __getitem__(self, name):
            return _Rels()

    root = generate_node_id("user_a", "User", "User")
    emotion = generate_node_id("user_a", "Emotion", "Anxiety")
    await upsert_relationship(
        _DB(),
        user_id="user_a",
        from_node_id=root,
        from_node_type="User",
        relation="EXPERIENCES",
        to_node_id=emotion,
        to_node_type="Emotion",
        properties={
            "intensity": 4,
            "narrative": "User said the journal entry out loud",
        },
    )
    assert captured["properties"] == {"intensity": 4}

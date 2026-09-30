"""Retrieval follows the question and stays inside one user's graph."""

import pytest

from exam_buddy_guardrails.memory.graph_repository import upsert_fact
from exam_buddy_guardrails.memory.graph_retriever import retrieve_memories
from exam_buddy_guardrails.memory.memory_context import render_memory_lines
from exam_buddy_guardrails.models import NodeType, RelationType
from exam_buddy_guardrails.tests.memory_db import MemoryDB


async def _seed(db, user_id: str):
    await upsert_fact(
        db, user_id, RelationType.STRUGGLES_WITH, NodeType.TOPIC, "quadratic equations"
    )
    await upsert_fact(
        db,
        user_id,
        RelationType.PREFERS,
        NodeType.LEARNING_PREFERENCE,
        "step-by-step explanations",
    )
    await upsert_fact(
        db, user_id, RelationType.MASTERED, NodeType.TOPIC, "linear equations"
    )
    await upsert_fact(
        db, user_id, RelationType.STRUGGLES_WITH, NodeType.SUBJECT, "chemistry"
    )


@pytest.mark.asyncio
async def test_quadratic_question_retrieves_related_learning_notes_only():
    db = MemoryDB()
    await _seed(db, "user_a")
    memories = await retrieve_memories(db, "user_a", "Can you explain quadratic equations again?")
    labels = {item.label for item in memories}
    assert "quadratic equations" in labels
    assert "step-by-step explanations" in labels
    assert "linear equations" not in labels
    assert "chemistry" not in labels
    text = " ".join(render_memory_lines(memories)).casefold()
    assert "struggled with quadratic equations" in text
    assert "prefers step-by-step explanations" in text
    assert "chemistry" not in text


@pytest.mark.asyncio
async def test_binary_search_does_not_pull_chemistry():
    db = MemoryDB()
    await upsert_fact(
        db, "user_a", RelationType.STRUGGLES_WITH, NodeType.SUBJECT, "chemistry"
    )
    memories = await retrieve_memories(db, "user_a", "Explain binary search.")
    assert memories == []


@pytest.mark.asyncio
async def test_thermodynamics_with_no_memory_is_empty():
    db = MemoryDB()
    assert await retrieve_memories(db, "user_a", "Explain thermodynamics.") == []

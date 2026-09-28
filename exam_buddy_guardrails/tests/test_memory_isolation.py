"""User A cannot read User B's learning graph."""

import pytest

from exam_buddy_guardrails.memory.graph_repository import upsert_fact
from exam_buddy_guardrails.memory.graph_retriever import retrieve_memories
from exam_buddy_guardrails.models import NodeType, RelationType
from exam_buddy_guardrails.tests.memory_db import MemoryDB


@pytest.mark.asyncio
async def test_retrieval_never_crosses_users():
    db = MemoryDB()
    await upsert_fact(
        db, "user_a", RelationType.STRUGGLES_WITH, NodeType.TOPIC, "quadratic equations"
    )
    await upsert_fact(
        db, "user_b", RelationType.STRUGGLES_WITH, NodeType.TOPIC, "organic chemistry"
    )
    memories = await retrieve_memories(db, "user_a", "Explain quadratic equations and organic chemistry.")
    labels = {item.label for item in memories}
    assert "quadratic equations" in labels
    assert "organic chemistry" not in labels

    other = await retrieve_memories(db, "user_b", "Explain quadratic equations.")
    assert {item.label for item in other} == set() or "quadratic equations" not in {
        item.label for item in other
    }
    assert all(item.label != "quadratic equations" for item in other)

"""Stable facts are stored once, and crisis text is not stored."""

import pytest

from exam_buddy_guardrails.config import NODES_COLLECTION
from exam_buddy_guardrails.memory.graph_repository import canonical_label, upsert_fact
from exam_buddy_guardrails.memory.memory_consolidator import consolidate_learning_memory
from exam_buddy_guardrails.memory.memory_extractor import extract_memories
from exam_buddy_guardrails.models import NodeType, RelationType
from exam_buddy_guardrails.tests.memory_db import MemoryDB


def test_quadratic_variants_share_one_label():
    assert canonical_label("quadratic equations") == "quadratic equations"
    assert canonical_label("Quadratic Equation") == "quadratic equations"
    assert canonical_label("quadratic equation problems") == "quadratic equations"


def test_step_preference_and_struggle_and_mastery_are_extracted():
    preference = extract_memories(
        "I always understand algebra better when you show the steps."
    )
    assert preference[0].relation is RelationType.PREFERS
    assert preference[0].node_type is NodeType.LEARNING_PREFERENCE

    struggle = extract_memories(
        "I keep making mistakes when solving quadratic equations."
    )
    assert struggle[0].relation is RelationType.STRUGGLES_WITH
    assert canonical_label(struggle[0].label) == "quadratic equations"

    mastered = extract_memories("I finally understand linear equations.")
    assert mastered[0].relation is RelationType.MASTERED
    assert canonical_label(mastered[0].label) == "linear equations"


def test_a_plain_question_is_not_stored():
    assert extract_memories("What is Newton's second law?") == []
    assert extract_memories("Explain thermodynamics.") == []


def test_crisis_text_is_not_extracted():
    assert extract_memories("I want to die. I struggle with algebra.") == []


@pytest.mark.asyncio
async def test_duplicate_labels_do_not_create_a_second_node():
    db = MemoryDB()
    await upsert_fact(
        db, "user_a", RelationType.STRUGGLES_WITH, NodeType.TOPIC, "Quadratic Equation"
    )
    await upsert_fact(
        db,
        "user_a",
        RelationType.STRUGGLES_WITH,
        NodeType.TOPIC,
        "quadratic equation problems",
    )
    topics = [
        doc
        for doc in db[NODES_COLLECTION].docs
        if doc.get("node_type") == "TOPIC"
    ]
    assert len(topics) == 1
    assert topics[0]["label"] == "quadratic equations"


@pytest.mark.asyncio
async def test_consolidator_skips_crisis_content():
    db = MemoryDB()
    written = await consolidate_learning_memory(
        db, "user_a", "I want to die and I keep making mistakes when solving algebra."
    )
    assert written == 0
    assert db[NODES_COLLECTION].docs == []

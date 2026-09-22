"""
tests/test_mongo_graph.py — Tests for MongoDB Graph Service
=============================================================

Tests:
  - Deterministic node ID generation
  - Node & relationship upserts
  - Strict user isolation
  - Subgraph traversal & formatting
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from schemas import GraphNodeLabel, GraphRelationType, GraphTuple
from services.mongo_graph import (
    ensure_graph_indexes,
    format_graph_for_prompt,
    generate_node_id,
    get_user_graph_context,
    upsert_relationship,
    upsert_graph_tuples,
)


def _create_mock_mongo_db():
    db = MagicMock()
    nodes_coll = MagicMock()
    rels_coll = MagicMock()

    collections = {
        "graph_nodes": nodes_coll,
        "graph_relationships": rels_coll,
    }
    db.__getitem__.side_effect = lambda name: collections.get(name, MagicMock())

    nodes_store = {}
    rels_store = {}

    async def create_index(keys, **kwargs):
        pass

    async def update_one_nodes(filter_query, update_doc, upsert=False):
        node_id = filter_query.get("node_id")
        if node_id and "$set" in update_doc:
            nodes_store[node_id] = update_doc["$set"]

    async def update_one_rels(filter_query, update_doc, upsert=False):
        user_id = filter_query.get("user_id")
        from_id = filter_query.get("from_node_id")
        to_id = filter_query.get("to_node_id")
        rel = filter_query.get("relation")
        key = (user_id, from_id, rel, to_id)
        if "$set" in update_doc:
            rels_store[key] = update_doc["$set"]

    nodes_coll.create_index = create_index
    rels_coll.create_index = create_index

    nodes_coll.update_one = update_one_nodes
    rels_coll.update_one = update_one_rels

    def aggregate_nodes(pipeline):
        match_step = pipeline[0].get("$match", {})
        user_id = match_step.get("user_id")

        matching_nodes = [
            doc for doc in nodes_store.values()
            if doc.get("user_id") == user_id and doc.get("node_type") != "User"
        ]

        records = []
        for n in matching_nodes:
            records.append({
                "rel_user_id": user_id,
                "target_type": n.get("node_type", "Entity"),
                "target_name": n.get("name"),
                "relation": "EXPERIENCES",
                "target_props": n.get("properties", {}),
            })

        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=records)
        return cursor

    nodes_coll.aggregate = aggregate_nodes
    return db


def test_generate_node_id_deterministic():
    id1 = generate_node_id("user_123", "Emotion", "Anxiety")
    id2 = generate_node_id("user_123", "Emotion", "Anxiety")
    id3 = generate_node_id("user_456", "Emotion", "Anxiety")

    assert id1 == id2
    assert id1 != id3
    assert id1.endswith("__emotion_anxiety")
    assert "user_123" not in id1


def test_node_identity_includes_type_and_canonical_name():
    anxiety = generate_node_id("user_123", "Emotion", " Anxiety ")
    same_anxiety = generate_node_id("user_123", "Emotion", "ANXIETY")
    trigger = generate_node_id("user_123", "Trigger", "Anxiety")

    assert anxiety == same_anxiety
    assert anxiety != trigger


@pytest.mark.asyncio
async def test_relationship_rejects_foreign_user_node_ids():
    mock_db = _create_mock_mongo_db()
    user_a_root = generate_node_id("user_A", "User", "User")
    user_b_emotion = generate_node_id("user_B", "Emotion", "Anxiety")

    with pytest.raises(ValueError, match="to_node_id does not belong"):
        await upsert_relationship(
            mock_db,
            user_id="user_A",
            from_node_id=user_a_root,
            from_node_type="User",
            relation="EXPERIENCES",
            to_node_id=user_b_emotion,
            to_node_type="Emotion",
        )


@pytest.mark.asyncio
async def test_ensure_graph_indexes():
    mock_db = _create_mock_mongo_db()
    await ensure_graph_indexes(mock_db)


@pytest.mark.asyncio
async def test_upsert_tuples_and_traversal():
    mock_db = _create_mock_mongo_db()

    tuples = [
        GraphTuple(
            source_node="User",
            source_label=GraphNodeLabel.USER,
            relationship=GraphRelationType.EXPERIENCES,
            target_node="Anxiety",
            target_label=GraphNodeLabel.EMOTION,
            properties={"intensity": 8},
        ),
        GraphTuple(
            source_node="Anxiety",
            source_label=GraphNodeLabel.EMOTION,
            relationship=GraphRelationType.TRIGGERED_BY,
            target_node="Public Speaking",
            target_label=GraphNodeLabel.TRIGGER,
        ),
    ]

    count = await upsert_graph_tuples(mock_db, "user_001", tuples)
    assert count == 2

    context_str = await get_user_graph_context(mock_db, "user_001", depth=2)
    assert "Anxiety" in context_str


@pytest.mark.asyncio
async def test_user_isolation():
    mock_db = _create_mock_mongo_db()

    tuples_a = [
        GraphTuple(
            source_node="User",
            source_label=GraphNodeLabel.USER,
            relationship=GraphRelationType.EXPERIENCES,
            target_node="Insomnia",
            target_label=GraphNodeLabel.EMOTION,
        )
    ]
    await upsert_graph_tuples(mock_db, "user_A", tuples_a)

    tuples_b = [
        GraphTuple(
            source_node="User",
            source_label=GraphNodeLabel.USER,
            relationship=GraphRelationType.EXPERIENCES,
            target_node="Euphoria",
            target_label=GraphNodeLabel.EMOTION,
        )
    ]
    await upsert_graph_tuples(mock_db, "user_B", tuples_b)

    context_a = await get_user_graph_context(mock_db, "user_A")
    assert "Insomnia" in context_a
    assert "Euphoria" not in context_a

    context_b = await get_user_graph_context(mock_db, "user_B")
    assert "Euphoria" in context_b
    assert "Insomnia" not in context_b


@pytest.mark.asyncio
async def test_traversal_pipeline_enforces_user_isolation_at_every_join():
    db = MagicMock()
    captured = {}

    def aggregate(pipeline):
        captured["pipeline"] = pipeline
        cursor = AsyncMock()
        cursor.to_list = AsyncMock(return_value=[])
        return cursor

    db["graph_nodes"].aggregate = aggregate
    await get_user_graph_context(db, "user_A")

    pipeline = captured["pipeline"]
    assert pipeline[0]["$match"]["user_id"] == "user_A"
    graph_lookup = pipeline[1]["$graphLookup"]
    assert graph_lookup["restrictSearchWithMatch"]["user_id"] == "user_A"

    node_lookup = pipeline[4]["$lookup"]["pipeline"][0]["$match"]["$expr"]["$and"]
    assert {"$eq": ["$user_id", "$$uid"]} in node_lookup


def test_format_graph_for_prompt():
    records = [
        {
            "rel_user_id": "u1",
            "target_type": "Emotion",
            "target_name": "Anxiety",
            "relation": "EXPERIENCES",
            "target_props": {"intensity": 8},
        }
    ]
    formatted = format_graph_for_prompt(records)
    assert "Emotional state: Anxiety" in formatted
    assert "intensity: 8" in formatted

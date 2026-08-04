"""
Unit tests for the Graph RAG service.

Covers:
    • format_graph_for_prompt — various record shapes
    • Cypher builder — correct MERGE statements
    • Empty graph fallback
    • Node key property mapping
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from schemas import GraphNodeLabel, GraphRelationType, GraphTuple
from services.graph_rag import (
    _build_upsert_cypher,
    _build_upsert_params,
    _format_graph_for_prompt,
    _node_key_property,
    upsert_graph_tuples,
)


# ── format_graph_for_prompt ─────────────────────────────────────────────────


def test_format_emotion_record():
    records = [
        {
            "node_labels": ["Emotion"],
            "name": None,
            "state": "Anxiety",
            "description": None,
            "title": None,
            "intensity": 8,
            "status": None,
            "relationship_chain": ["EXPERIENCES"],
            "tool_name": None,
        }
    ]
    result = _format_graph_for_prompt(records)

    assert "Emotional state" in result
    assert "Anxiety" in result
    assert "intensity: 8" in result


def test_format_entity_with_tool():
    records = [
        {
            "node_labels": ["Entity"],
            "name": "Mother",
            "state": None,
            "description": None,
            "title": None,
            "intensity": None,
            "status": None,
            "relationship_chain": ["ASSOCIATED_WITH"],
            "tool_name": "8-Min Body Scan",
        }
    ]
    result = _format_graph_for_prompt(records)

    assert "Related person/thing" in result
    assert "Mother" in result
    assert "8-Min Body Scan" in result


def test_format_event_with_status():
    records = [
        {
            "node_labels": ["Event"],
            "name": None,
            "state": None,
            "description": None,
            "title": "Job Interview",
            "intensity": None,
            "status": "Upcoming",
            "relationship_chain": ["PARTICIPATED_IN"],
            "tool_name": None,
        }
    ]
    result = _format_graph_for_prompt(records)

    assert "Life event" in result
    assert "Job Interview" in result
    assert "[Upcoming]" in result


def test_format_coping_tool():
    records = [
        {
            "node_labels": ["CopingTool"],
            "name": "Box Breathing",
            "state": None,
            "description": None,
            "title": None,
            "intensity": None,
            "status": None,
            "relationship_chain": ["TRIED_TOOL", "HELPED_WITH"],
            "tool_name": None,
        }
    ]
    result = _format_graph_for_prompt(records)

    assert "Coping tool" in result
    assert "Box Breathing" in result


def test_format_multiple_records_deduplicated():
    records = [
        {
            "node_labels": ["Trigger"],
            "name": None,
            "state": None,
            "description": "Work Deadlines",
            "title": None,
            "intensity": None,
            "status": None,
            "relationship_chain": ["TRIGGERED_BY"],
            "tool_name": None,
        },
        # Duplicate
        {
            "node_labels": ["Trigger"],
            "name": None,
            "state": None,
            "description": "Work Deadlines",
            "title": None,
            "intensity": None,
            "status": None,
            "relationship_chain": ["TRIGGERED_BY"],
            "tool_name": None,
        },
    ]
    result = _format_graph_for_prompt(records)
    assert result.count("Work Deadlines") == 1


def test_format_empty_records():
    result = _format_graph_for_prompt([])
    assert result == "No relational graph data available yet."


# ── Cypher Builder ──────────────────────────────────────────────────────────


def test_build_upsert_cypher_user_experiences_emotion():
    t = GraphTuple(
        source_node="User",
        source_label=GraphNodeLabel.USER,
        relationship=GraphRelationType.EXPERIENCES,
        target_node="Anxiety",
        target_label=GraphNodeLabel.EMOTION,
    )
    cypher = _build_upsert_cypher(t)

    assert "MERGE (src:User {id: $src_val})" in cypher
    assert "MERGE (tgt:Emotion {state: $tgt_val})" in cypher
    assert "MERGE (src)-[r:EXPERIENCES]->(tgt)" in cypher
    assert "SET r.last_updated = $now" in cypher


def test_build_upsert_cypher_entity_trigger():
    t = GraphTuple(
        source_node="Work Deadlines",
        source_label=GraphNodeLabel.TRIGGER,
        relationship=GraphRelationType.ASSOCIATED_WITH,
        target_node="Manager",
        target_label=GraphNodeLabel.ENTITY,
    )
    cypher = _build_upsert_cypher(t)

    assert "MERGE (src:Trigger {description: $src_val})" in cypher
    assert "MERGE (tgt:Entity {name: $tgt_val})" in cypher


def test_build_upsert_params_user_source():
    t = GraphTuple(
        source_node="User",
        source_label=GraphNodeLabel.USER,
        relationship=GraphRelationType.EXPERIENCES,
        target_node="Sadness",
        target_label=GraphNodeLabel.EMOTION,
        properties={"intensity": 6},
    )
    params = _build_upsert_params("user_123", t)

    # User nodes use user_id as the key, not the node name
    assert params["src_val"] == "user_123"
    assert params["tgt_val"] == "Sadness"
    assert params["props"] == {"intensity": 6}
    assert "now" in params


# ── Node Key Property ──────────────────────────────────────────────────────


def test_node_key_properties():
    assert _node_key_property("User") == "id"
    assert _node_key_property("Entity") == "name"
    assert _node_key_property("Emotion") == "state"
    assert _node_key_property("Trigger") == "description"
    assert _node_key_property("CopingTool") == "name"
    assert _node_key_property("Event") == "title"
    assert _node_key_property("Session") == "session_id"
    assert _node_key_property("Unknown") == "name"  # fallback


# ── upsert_graph_tuples (with mocked driver) ───────────────────────────────


@pytest.mark.asyncio
async def test_upsert_graph_tuples_success():
    tuples = [
        GraphTuple(
            source_node="User",
            source_label=GraphNodeLabel.USER,
            relationship=GraphRelationType.EXPERIENCES,
            target_node="Anxiety",
            target_label=GraphNodeLabel.EMOTION,
        ),
        GraphTuple(
            source_node="Anxiety",
            source_label=GraphNodeLabel.EMOTION,
            relationship=GraphRelationType.TRIGGERED_BY,
            target_node="Public Speaking",
            target_label=GraphNodeLabel.TRIGGER,
        ),
    ]

    mock_session = AsyncMock()
    mock_session.run = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_driver = MagicMock()
    mock_driver.session = MagicMock(return_value=mock_session)

    count = await upsert_graph_tuples(mock_driver, "user_001", tuples)

    assert count == 2
    # 1 MERGE for User node + 2 MERGE for tuples = 3 session.run calls
    assert mock_session.run.call_count == 3


@pytest.mark.asyncio
async def test_upsert_graph_tuples_empty():
    mock_driver = MagicMock()
    count = await upsert_graph_tuples(mock_driver, "user_001", [])
    assert count == 0

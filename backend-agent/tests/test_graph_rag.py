"""
Unit tests for the Graph RAG service (MongoDB backed).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from schemas import GraphNodeLabel, GraphRelationType, GraphTuple
from services.graph_rag import (
    _format_graph_for_prompt,
    get_user_emotional_graph,
    upsert_graph_tuples,
)


def test_format_emotion_record():
    records = [
        {
            "rel_user_id": "u1",
            "target_type": "Emotion",
            "target_name": "Anxiety",
            "relation": "EXPERIENCES",
            "target_props": {"intensity": 8},
        }
    ]
    result = _format_graph_for_prompt(records)

    assert "Emotional state" in result
    assert "Anxiety" in result
    assert "intensity: 8" in result


def test_format_entity_with_tool():
    records = [
        {
            "rel_user_id": "u1",
            "target_type": "Entity",
            "target_name": "Mother",
            "relation": "ASSOCIATED_WITH",
            "target_props": {},
            "rel_props": {"tool_name": "8-Min Body Scan"},
        }
    ]
    result = _format_graph_for_prompt(records)

    assert "Related person/thing" in result
    assert "Mother" in result


def test_format_empty_records():
    result = _format_graph_for_prompt([])
    assert result == "No relational graph data available yet."


@pytest.mark.asyncio
async def test_upsert_graph_tuples_success():
    mock_db = MagicMock()
    mock_db["graph_nodes"].update_one = AsyncMock()
    mock_db["graph_relationships"].update_one = AsyncMock()

    cursor = AsyncMock()
    cursor.to_list = AsyncMock(return_value=[{
        "rel_user_id": "user_001",
        "target_type": "Emotion",
        "target_name": "Anxiety",
        "relation": "EXPERIENCES",
        "target_props": {},
    }])
    mock_db["graph_nodes"].aggregate = MagicMock(return_value=cursor)

    tuples = [
        GraphTuple(
            source_node="User",
            source_label=GraphNodeLabel.USER,
            relationship=GraphRelationType.EXPERIENCES,
            target_node="Anxiety",
            target_label=GraphNodeLabel.EMOTION,
        ),
    ]

    count = await upsert_graph_tuples(mock_db, "user_001", tuples)
    assert count == 1

    context = await get_user_emotional_graph(mock_db, "user_001")
    assert "Anxiety" in context


@pytest.mark.asyncio
async def test_upsert_graph_tuples_empty():
    mock_db = MagicMock()
    count = await upsert_graph_tuples(mock_db, "user_001", [])
    assert count == 0

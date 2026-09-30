"""Tests for resilient graph-tuple sanitization."""

from schemas import GraphRelationType
from services.extraction import sanitize_graph_payload


def test_sanitize_maps_asked_to_associated_with():
    data = sanitize_graph_payload(
        {
            "tuples": [
                {
                    "source_node": "User",
                    "source_label": "User",
                    "relationship": "ASKED",
                    "target_node": "Trust",
                    "target_label": "Emotion",
                    "properties": {},
                },
                {
                    "source_node": "User",
                    "source_label": "User",
                    "relationship": "EXPERIENCES",
                    "target_node": "curiosity",
                    "target_label": "Emotion",
                    "properties": {},
                },
            ]
        }
    )
    assert len(data.tuples) == 2
    assert data.tuples[0].relationship == GraphRelationType.ASSOCIATED_WITH
    assert data.tuples[1].relationship == GraphRelationType.EXPERIENCES


def test_sanitize_drops_unrecoverable_relation():
    data = sanitize_graph_payload(
        {
            "tuples": [
                {
                    "source_node": "User",
                    "source_label": "User",
                    "relationship": "TELEPORTED_TO",
                    "target_node": "Mars",
                    "target_label": "Entity",
                }
            ]
        }
    )
    assert data.tuples == []


def test_sanitize_handles_missing_tuples_key():
    assert sanitize_graph_payload({}).tuples == []
    assert sanitize_graph_payload({"tuples": None}).tuples == []

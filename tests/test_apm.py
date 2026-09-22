"""Adaptive Psychological Memory safety, isolation, and learning tests."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from schemas import (
    APMExtraction,
    APMNodeType,
    APMObservation,
    APMRelationType,
    APMTransition,
)
from services.apm import (
    bootstrap_existing_graph,
    decay_rate,
    delete_adaptive_memory,
    effective_edge_score,
    ensure_apm_indexes,
    evaluate_jitai_candidate,
    get_recovery_paths,
    make_apm_edge_id,
    make_apm_node_id,
    persist_apm_extraction,
    record_intervention_feedback,
    temporal_bucket,
)


@pytest_asyncio.fixture
async def db():
    database = AsyncMongoMockClient()["apm_test"]
    await ensure_apm_indexes(database)
    await database["users"].insert_many(
        [
            {
                "user_id": "user_A",
                "isActive": True,
                "personalization_consent": True,
            },
            {
                "user_id": "user_B",
                "isActive": True,
                "personalization_consent": True,
            },
            {
                "user_id": "no_consent",
                "isActive": True,
                "personalization_consent": False,
            },
        ]
    )
    return database


def test_apm_identity_is_deterministic_and_user_scoped():
    a1 = make_apm_node_id("user_A", APMNodeType.LATENT_STATE, "High Fatigue")
    a2 = make_apm_node_id("user_A", APMNodeType.LATENT_STATE, " high  fatigue ")
    b = make_apm_node_id("user_B", APMNodeType.LATENT_STATE, "High Fatigue")
    assert a1 == a2
    assert a1 != b
    assert "user_A" not in a1


@pytest.mark.asyncio
async def test_extraction_is_consent_gated_and_crisis_excluded(db):
    extraction = APMExtraction(
        observations=[
            APMObservation(
                node_type=APMNodeType.LATENT_STATE,
                label="High fatigue",
                confidence_score=0.8,
            )
        ]
    )
    assert (
        await persist_apm_extraction(
            db, "no_consent", "session_1", extraction
        )
        == 0
    )
    crisis = extraction.model_copy(update={"crisis_signal_detected": True})
    assert await persist_apm_extraction(db, "user_A", "session_2", crisis) == 0
    assert await db["apm_nodes"].count_documents({}) == 0


@pytest.mark.asyncio
async def test_same_labels_never_cross_user_boundary(db):
    extraction = APMExtraction(
        observations=[
            APMObservation(
                node_type=APMNodeType.LATENT_STATE,
                label="Exam fatigue",
                confidence_score=0.8,
            ),
            APMObservation(
                node_type=APMNodeType.INTERVENTION,
                label="One minute grounding",
                confidence_score=0.8,
            ),
        ],
        transitions=[
            APMTransition(
                source_type=APMNodeType.LATENT_STATE,
                source_label="Exam fatigue",
                target_type=APMNodeType.INTERVENTION,
                target_label="One minute grounding",
                relation_type=APMRelationType.RECOVERED_BY,
                confidence_score=0.8,
            )
        ],
    )
    await persist_apm_extraction(db, "user_A", "session_A", extraction)
    await persist_apm_extraction(db, "user_B", "session_B", extraction)

    nodes_a = await db["apm_nodes"].find({"user_id": "user_A"}).to_list(None)
    nodes_b = await db["apm_nodes"].find({"user_id": "user_B"}).to_list(None)
    assert {node["node_id"] for node in nodes_a}.isdisjoint(
        {node["node_id"] for node in nodes_b}
    )


async def _seed_recovery_path(
    db,
    user_id: str,
    *,
    confidence: float,
    successes: int,
    last_outcome: str = "SUCCESS",
):
    state_id = make_apm_node_id(
        user_id, APMNodeType.LATENT_STATE, "High cognitive fatigue"
    )
    tool_id = make_apm_node_id(
        user_id, APMNodeType.INTERVENTION, "Micro grounding"
    )
    edge_id = make_apm_edge_id(
        user_id, state_id, APMRelationType.RECOVERED_BY, tool_id
    )
    now = datetime.now(timezone.utc)
    await db["apm_nodes"].insert_many(
        [
            {
                "user_id": user_id,
                "node_id": state_id,
                "node_type": "LATENT_STATE",
                "canonical_label": "high cognitive fatigue",
                "display_label": "High cognitive fatigue",
                "aliases": ["fried", "brain fog"],
                "confidence_score": confidence,
                "temporal_buckets": [temporal_bucket(now)],
                "last_seen_at": now,
            },
            {
                "user_id": user_id,
                "node_id": tool_id,
                "node_type": "INTERVENTION",
                "canonical_label": "micro grounding",
                "display_label": "Micro grounding",
                "aliases": ["grounding"],
                "confidence_score": confidence,
                "temporal_buckets": [temporal_bucket(now)],
                "last_seen_at": now,
            },
        ]
    )
    await db["apm_edges"].insert_one(
        {
            "user_id": user_id,
            "edge_id": edge_id,
            "source_node_id": state_id,
            "target_node_id": tool_id,
            "relation_type": "RECOVERED_BY",
            "confidence_score": confidence,
            "explicit_successes": successes,
            "explicit_failures": 0,
            "bayesian_score": 0.75,
            "decay_rate": decay_rate(APMRelationType.RECOVERED_BY),
            "last_outcome": last_outcome,
            "updated_at": now,
            "version": 1,
        }
    )
    return edge_id, tool_id


@pytest.mark.asyncio
async def test_card_threshold_and_temporal_fallback_are_distinct(db):
    await _seed_recovery_path(db, "user_A", confidence=0.39, successes=2)
    low = await get_recovery_paths(db, "user_A", "my brain feels fried")
    assert len(low) == 1
    assert low[0]["recommendation_eligible"] is False

    await db["apm_nodes"].delete_many({"user_id": "user_A"})
    await db["apm_edges"].delete_many({"user_id": "user_A"})
    await _seed_recovery_path(db, "user_A", confidence=0.8, successes=2)
    direct = await get_recovery_paths(db, "user_A", "my brain feels fried")
    assert direct[0]["recommendation_eligible"] is True
    assert direct[0]["inferred"] is False

    fallback = await get_recovery_paths(db, "user_A", "xyzzy novel slang")
    assert fallback[0]["inferred"] is True
    assert fallback[0]["recommendation_eligible"] is False
    assert await get_recovery_paths(db, "user_A", "I want to die") == []


@pytest.mark.asyncio
async def test_feedback_is_idempotent_and_atomic(db):
    edge_id, tool_id = await _seed_recovery_path(
        db, "user_A", confidence=0.8, successes=1
    )
    kwargs = dict(
        db=db,
        user_id="user_A",
        edge_id=edge_id,
        intervention_id=tool_id,
        execution_nonce="nonce-1",
        event_type="HELPFUL",
    )
    results = await asyncio.gather(
        record_intervention_feedback(**kwargs),
        record_intervention_feedback(**kwargs),
    )
    assert sorted(results) == [False, True]
    edge = await db["apm_edges"].find_one({"edge_id": edge_id})
    assert edge["explicit_successes"] == 2
    assert edge["version"] == 2


@pytest.mark.asyncio
async def test_feedback_cannot_reinforce_another_users_edge(db):
    edge_id, tool_id = await _seed_recovery_path(
        db, "user_B", confidence=0.8, successes=1
    )
    with pytest.raises(ValueError, match="does not belong"):
        await record_intervention_feedback(
            db,
            "user_A",
            edge_id=edge_id,
            intervention_id=tool_id,
            execution_nonce="foreign",
            event_type="HELPFUL",
        )


def test_relation_specific_decay_and_failure_penalty():
    assert decay_rate(APMRelationType.TRIGGERS) < decay_rate(
        APMRelationType.RECOVERED_BY
    )
    old = datetime.now(timezone.utc) - timedelta(days=30)
    success = {
        "bayesian_score": 0.8,
        "decay_rate": 0.02,
        "updated_at": old,
        "last_outcome": "SUCCESS",
    }
    failed = {**success, "last_outcome": "FAILURE"}
    assert effective_edge_score(failed) < effective_edge_score(success)


@pytest.mark.asyncio
async def test_bootstrap_remains_background_only(db):
    await db["graph_nodes"].insert_many(
        [
            {
                "user_id": "user_A",
                "node_id": "legacy_emotion",
                "node_type": "Emotion",
                "name": "Anxiety",
            },
            {
                "user_id": "user_A",
                "node_id": "legacy_tool",
                "node_type": "CopingTool",
                "name": "Box breathing",
            },
        ]
    )
    await db["graph_relationships"].insert_one(
        {
            "user_id": "user_A",
            "from_node_id": "legacy_tool",
            "to_node_id": "legacy_emotion",
            "relation": "HELPED_WITH",
        }
    )
    assert await bootstrap_existing_graph(db, "user_A") == 3
    edge = await db["apm_edges"].find_one({"user_id": "user_A"})
    assert edge["confidence_score"] < 0.4
    assert edge["explicit_successes"] == 0


@pytest.mark.asyncio
async def test_deletion_and_disabled_jitai_boundary(db):
    await _seed_recovery_path(db, "user_A", confidence=0.8, successes=1)
    jitai = await evaluate_jitai_candidate(db, "user_A", "brain fog")
    assert jitai["candidate_detected"] is True
    assert jitai["outbound_enabled"] is False

    deleted = await delete_adaptive_memory(db, "user_A")
    assert deleted["apm_nodes"] == 2
    assert await db["apm_edges"].count_documents({"user_id": "user_A"}) == 0

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
    get_adaptive_memory_context,
    get_recovery_paths,
    get_relevant_associations,
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
    assert deleted.get("apm_episodes", 0) == 0


def _presentation_extraction(*, helpful: bool = False) -> APMExtraction:
    observations = [
        APMObservation(
            node_type=APMNodeType.TRIGGER,
            label="presentation",
            valence=-0.6,
            intensity=0.7,
            evidence_kind="explicit",
            confidence_score=0.8,
        ),
        APMObservation(
            node_type=APMNodeType.LATENT_STATE,
            label="overwhelmed",
            valence=-0.6,
            intensity=0.7,
            evidence_kind="explicit",
            confidence_score=0.8,
        ),
        APMObservation(
            node_type=APMNodeType.CONTEXT,
            label="before exams",
            evidence_kind="explicit",
            confidence_score=0.7,
        ),
        APMObservation(
            node_type=APMNodeType.INTERVENTION,
            label="talking to a friend",
            evidence_kind="explicit",
            confidence_score=0.7,
        ),
    ]
    transitions = [
        APMTransition(
            source_type=APMNodeType.TRIGGER,
            source_label="presentation",
            target_type=APMNodeType.LATENT_STATE,
            target_label="overwhelmed",
            relation_type=APMRelationType.TRIGGERS,
            confidence_score=0.8,
        ),
    ]
    if helpful:
        observations.append(
            APMObservation(
                node_type=APMNodeType.OUTCOME,
                label="user reported feeling calmer",
                valence=0.3,
                intensity=0.2,
                evidence_kind="explicit",
                confidence_score=0.8,
            )
        )
        transitions.append(
            APMTransition(
                source_type=APMNodeType.LATENT_STATE,
                source_label="overwhelmed",
                target_type=APMNodeType.INTERVENTION,
                target_label="talking to a friend",
                relation_type=APMRelationType.RECOVERED_BY,
                confidence_score=0.8,
            )
        )
    return APMExtraction(observations=observations, transitions=transitions)


@pytest.mark.asyncio
async def test_episode_stores_valence_intensity_context_and_source(db):
    message = "I always get overwhelmed before presentations."
    written = await persist_apm_extraction(
        db,
        "user_A",
        "sess_1",
        _presentation_extraction(),
        message=message,
        source_message_id="msg_1",
    )
    assert written > 0
    episode = await db["apm_episodes"].find_one({"user_id": "user_A"})
    assert episode is not None
    assert episode["valence"] == pytest.approx(-0.6)
    assert episode["intensity"] == pytest.approx(0.7)
    assert episode["context"] == "ACADEMIC"
    assert episode["explicitness"] == "explicit"
    assert episode["source_message_id"] == "msg_1"
    assert episode["source_reference"]["kind"] == "chat_turn"
    assert message not in str(episode)
    trigger = await db["apm_nodes"].find_one(
        {"user_id": "user_A", "node_type": "TRIGGER"}
    )
    assert trigger["display_label"].startswith("enc::")
    assert trigger["canonical_label"] == "presentation"
    assert trigger["source_kind"] == "explicit"
    assert trigger["occurrence_count"] == 1


@pytest.mark.asyncio
async def test_recurrence_increments_only_matching_evidence(db):
    first = _presentation_extraction()
    await persist_apm_extraction(db, "user_A", "sess_1", first, message="presentations overwhelm me")
    await persist_apm_extraction(db, "user_A", "sess_1", first, message="presentations overwhelm me")
    trigger = await db["apm_nodes"].find_one(
        {"user_id": "user_A", "canonical_label": "presentation"}
    )
    assert trigger["occurrence_count"] == 1
    await persist_apm_extraction(
        db, "user_A", "sess_2", first, message="another presentation is coming"
    )
    trigger = await db["apm_nodes"].find_one(
        {"user_id": "user_A", "canonical_label": "presentation"}
    )
    assert trigger["occurrence_count"] == 2
    unrelated = APMExtraction(
        observations=[
            APMObservation(
                node_type=APMNodeType.TRIGGER,
                label="family argument",
                evidence_kind="explicit",
                confidence_score=0.7,
            )
        ]
    )
    await persist_apm_extraction(
        db, "user_A", "sess_3", unrelated, message="we had a family argument"
    )
    trigger = await db["apm_nodes"].find_one(
        {"user_id": "user_A", "canonical_label": "presentation"}
    )
    assert trigger["occurrence_count"] == 2
    family = await db["apm_nodes"].find_one(
        {"user_id": "user_A", "canonical_label": "family argument"}
    )
    assert family["occurrence_count"] == 1


@pytest.mark.asyncio
async def test_coping_sequence_and_false_success_rejected(db):
    await persist_apm_extraction(
        db,
        "user_A",
        "sess_help",
        _presentation_extraction(helpful=True),
        message="talking to a friend helped and I felt calmer after the presentation",
    )
    edge = await db["apm_edges"].find_one(
        {"user_id": "user_A", "relation_type": "RECOVERED_BY"}
    )
    assert edge is not None
    assert edge["explicit_successes"] == 0
    await record_intervention_feedback(
        db,
        "user_A",
        edge_id=edge["edge_id"],
        intervention_id=edge["target_node_id"],
        execution_nonce="open-1",
        event_type="STARTED",
    )
    edge = await db["apm_edges"].find_one({"edge_id": edge["edge_id"]})
    assert edge["explicit_successes"] == 0
    await record_intervention_feedback(
        db,
        "user_A",
        edge_id=edge["edge_id"],
        intervention_id=edge["target_node_id"],
        execution_nonce="help-1",
        event_type="HELPFUL",
    )
    edge = await db["apm_edges"].find_one({"edge_id": edge["edge_id"]})
    assert edge["explicit_successes"] == 1


@pytest.mark.asyncio
async def test_later_turn_retrieves_apm_and_current_correction_wins(db):
    await persist_apm_extraction(
        db,
        "user_A",
        "sess_1",
        _presentation_extraction(),
        message="I always get overwhelmed before presentations",
    )
    later = await get_adaptive_memory_context(
        db, "user_A", "I've got another presentation tomorrow"
    )
    assert "presentation" in later.lower()
    assert "overwhelmed" in later.lower()
    assert "enc::" not in later
    denied = await get_adaptive_memory_context(
        db, "user_A", "Presentations aren't stressful anymore"
    )
    assert "presentation" not in denied.lower()
    from services.apm import apply_user_correction

    assert await apply_user_correction(
        db, "user_A", "Presentations aren't stressful anymore"
    )
    node = await db["apm_nodes"].find_one(
        {"user_id": "user_A", "canonical_label": "presentation"}
    )
    assert node["status"] == "INVALIDATED"
    after = await get_relevant_associations(
        db, "user_A", "I've got another presentation tomorrow"
    )
    assert after == []


@pytest.mark.asyncio
async def test_consent_crisis_and_cross_user_isolation_for_associations(db):
    extraction = _presentation_extraction()
    await persist_apm_extraction(
        db, "user_A", "sess_a", extraction, message="presentations overwhelm me"
    )
    await persist_apm_extraction(
        db, "user_B", "sess_b", extraction, message="presentations overwhelm me"
    )
    a_labels = {
        item["label"]
        for item in await get_relevant_associations(
            db, "user_A", "presentation tomorrow"
        )
    }
    b_context = await get_adaptive_memory_context(db, "user_B", "family conflict")
    assert any("presentation" in label.lower() for label in a_labels)
    assert "presentation" not in b_context.lower()
    assert await persist_apm_extraction(
        db, "no_consent", "sess_x", extraction, message="presentations overwhelm me"
    ) == 0
    crisis = extraction.model_copy(update={"crisis_signal_detected": True})
    assert await persist_apm_extraction(
        db, "user_A", "sess_crisis", crisis, message="presentations overwhelm me"
    ) == 0


@pytest.mark.asyncio
async def test_erasure_removes_episodes_and_blocks_future_retrieval(db):
    from services.erasure import start_erasure

    await persist_apm_extraction(
        db, "user_A", "sess_1", _presentation_extraction(), message="presentations overwhelm me"
    )
    assert await db["apm_episodes"].count_documents({"user_id": "user_A"}) == 1
    result = await start_erasure(db, "user_A")
    assert result["status"] == "succeeded"
    assert await db["apm_nodes"].count_documents({"user_id": "user_A"}) == 0
    assert await db["apm_episodes"].count_documents({"user_id": "user_A"}) == 0
    later = await get_adaptive_memory_context(
        db, "user_A", "I've got another presentation tomorrow"
    )
    assert later == "No adaptive psychological memory available."


@pytest.mark.asyncio
async def test_proactive_consumes_apm_until_user_corrects(db):
    from services.proactive.service import evaluate_proactive_question
    from services.proactive.schemas import Decision

    await persist_apm_extraction(
        db,
        "user_A",
        "sess_1",
        _presentation_extraction(),
        message="I always get overwhelmed before presentations",
    )
    asked = await evaluate_proactive_question(
        db, "user_A", opening_turn=True, dispatch=False
    )
    assert asked.decision == Decision.PROACTIVE_QUESTION.value
    assert "presentation" in asked.question.lower()
    from services.apm import apply_user_correction

    await apply_user_correction(db, "user_A", "Presentations aren't stressful anymore")
    after = await evaluate_proactive_question(
        db,
        "user_A",
        opening_turn=True,
        dispatch=False,
        now=datetime.now(timezone.utc) + timedelta(days=4),
    )
    if after.decision == Decision.PROACTIVE_QUESTION.value:
        assert "presentation" not in after.question.lower()
    else:
        assert after.reason in {
            "empty_graph",
            "no_justified_trigger",
            "low_receptivity",
        }

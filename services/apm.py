"""
Consent-gated Adaptive Psychological Memory (APM).

APM is intentionally separate from the general relationship graph. It stores
stable psychological concepts, temporal transitions, and explicit intervention
outcomes. It never treats engagement or message volume as evidence of relief.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from config.config import get_settings, logger
from core.logging import hash_user_id
from schemas import (
    APMExtraction,
    APMNodeType,
    APMRelationType,
    APMTransition,
)
from services.telemetry import register_metric

EMPTY_APM_CONTEXT = "No adaptive psychological memory available."
_CRISIS_TERMS = (
    "suicide",
    "kill myself",
    "hurt myself",
    "cut myself",
    "end my life",
    "want to die",
    "no reason to live",
    "wish i wouldn't wake up",
    "self harm",
    "overdose",
)
_FEEDBACK_EVENTS = frozenset({"STARTED", "COMPLETED", "HELPFUL", "NOT_HELPFUL"})
_STATUS_ACTIVE = "ACTIVE"
_STATUS_INVALIDATED = "INVALIDATED"
_CORRECTION_MARKERS = (
    "that's not what happened",
    "that is not what happened",
    "that's not true",
    "that is not true",
    "not stressful anymore",
    "aren't stressful anymore",
    "are not stressful anymore",
    "isn't stressful anymore",
    "is not stressful",
    "aren't stressful",
    "no longer stressful",
)
_CONTEXT_HINTS = (
    ("ACADEMIC", ("exam", "test", "school", "marks", "assignment", "presentation", "grade", "study")),
    ("FAMILY", ("parent", "mom", "dad", "family", "home")),
    ("FRIENDSHIP", ("friend", "friends", "classmate")),
    ("BULLYING", ("bully", "bullying", "teased")),
    ("SLEEP", ("sleep", "insomnia", "tired", "rest")),
    ("IDENTITY", ("identity", "who i am")),
    ("SOCIAL_MEDIA", ("instagram", "social media", "likes")),
    ("PERFORMANCE", ("performance", "stage", "audience")),
    ("RELATIONSHIP", ("relationship", "partner", "breakup")),
    ("SELF_IMAGE", ("looks", "body", "self-image")),
    ("WORK", ("internship", "job", "work")),
    ("HEALTH", ("health", "sick", "illness")),
)


def temporal_bucket(at: Optional[datetime] = None) -> str:
    """Return a coarse UTC bucket; user-local timezone can replace UTC later."""
    hour = (at or datetime.now(timezone.utc)).hour
    if hour < 5:
        return "LATE_NIGHT"
    if hour < 12:
        return "MORNING"
    if hour < 17:
        return "AFTERNOON"
    if hour < 22:
        return "EVENING"
    return "LATE_NIGHT"


def contains_crisis_signal(text: str) -> bool:
    lowered = (text or "").casefold()
    return any(term in lowered for term in _CRISIS_TERMS)


def _message_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z]{3,}", (text or "").casefold())
        if token
    }


def labels_overlap(message: str, label: str) -> bool:
    left = _message_tokens(message)
    right = _message_tokens(label)
    if left & right:
        return True
    for first in left:
        for second in right:
            if min(len(first), len(second)) >= 5 and (
                first.startswith(second) or second.startswith(first)
            ):
                return True
    return False


def message_contradicts_topic(message: str, label: str) -> bool:
    """Current-turn denial of a remembered topic. Not a diagnosis check."""
    if not labels_overlap(message, label):
        return False
    lowered = (message or "").casefold()
    return any(marker in lowered for marker in _CORRECTION_MARKERS)


def normalize_context_bucket(label: str) -> str:
    lowered = (label or "").casefold()
    for bucket, hints in _CONTEXT_HINTS:
        if any(hint in lowered for hint in hints):
            return bucket
    return "OTHER"


def _blocked_psychological_label(label: str) -> bool:
    if contains_crisis_signal(label):
        return True
    lowered = (label or "").casefold()
    return any(
        term in lowered
        for term in (
            "generalized anxiety",
            "major depression",
            "disorder",
            "self-harm method",
            "how to cut",
            "overdose",
        )
    )


def make_observation_nonce(user_id: str, session_id: str, message: str) -> str:
    material = f"{user_id}|{session_id}|{(message or '').strip()}"
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def _open_label(value: Any) -> str:
    from services.security import open_text

    return str(open_text(value or "") or "").strip()


def recurrence_confidence(occurrence_count: int) -> float:
    count = max(0, int(occurrence_count or 0))
    if count <= 1:
        return 0.0
    return min(1.0, (count - 1) / 3.0)


def _owner_namespace(user_id: str) -> str:
    if not user_id or not user_id.strip():
        raise ValueError("user_id is required")
    return sha256(user_id.strip().encode("utf-8")).hexdigest()[:16]


def _canonical_label(label: str) -> str:
    canonical = re.sub(r"\s+", " ", (label or "").strip().casefold())
    if not canonical:
        raise ValueError("APM label must not be empty")
    return canonical


def make_apm_node_id(user_id: str, node_type: APMNodeType | str, label: str) -> str:
    node_type_value = (
        node_type.value if isinstance(node_type, APMNodeType) else str(node_type)
    )
    slug = re.sub(r"[^\w]+", "_", _canonical_label(label)).strip("_")
    return f"apm_{_owner_namespace(user_id)}__{node_type_value.casefold()}__{slug}"


def make_apm_edge_id(
    user_id: str,
    source_node_id: str,
    relation_type: APMRelationType | str,
    target_node_id: str,
) -> str:
    relation = (
        relation_type.value
        if isinstance(relation_type, APMRelationType)
        else str(relation_type)
    )
    material = f"{source_node_id}|{relation}|{target_node_id}"
    digest = sha256(material.encode("utf-8")).hexdigest()[:24]
    return f"apme_{_owner_namespace(user_id)}__{digest}"


def _assert_owned_id(user_id: str, object_id: str, prefix: str) -> None:
    expected = f"{prefix}_{_owner_namespace(user_id)}__"
    if not object_id.startswith(expected):
        raise ValueError("APM object does not belong to authenticated user")


def decay_rate(relation_type: APMRelationType | str) -> float:
    settings = get_settings()
    relation = (
        relation_type.value
        if isinstance(relation_type, APMRelationType)
        else str(relation_type)
    )
    return {
        APMRelationType.TRIGGERS.value: settings.APM_TRIGGER_DECAY_PER_DAY,
        APMRelationType.EVOLVES_INTO.value: settings.APM_EVOLUTION_DECAY_PER_DAY,
        APMRelationType.RECOVERED_BY.value: settings.APM_RECOVERY_DECAY_PER_DAY,
        APMRelationType.REINFORCES.value: settings.APM_REINFORCEMENT_DECAY_PER_DAY,
    }[relation]


async def ensure_apm_indexes(db: AsyncIOMotorDatabase) -> None:
    await db["apm_nodes"].create_index(
        [("user_id", 1), ("node_id", 1)], unique=True, name="uniq_apm_user_node"
    )
    await db["apm_nodes"].create_index(
        [
            ("user_id", 1),
            ("node_type", 1),
            ("temporal_buckets", 1),
            ("last_seen_at", -1),
        ],
        name="idx_apm_recent_state",
    )
    await db["apm_nodes"].create_index(
        [("user_id", 1), ("canonical_label", 1)], name="idx_apm_alias_lookup"
    )
    await db["apm_edges"].create_index(
        [("user_id", 1), ("edge_id", 1)], unique=True, name="uniq_apm_user_edge"
    )
    await db["apm_edges"].create_index(
        [
            ("user_id", 1),
            ("source_node_id", 1),
            ("relation_type", 1),
            ("bayesian_score", -1),
        ],
        name="idx_apm_recovery_lookup",
    )
    await db["apm_events"].create_index(
        [("user_id", 1), ("execution_nonce", 1), ("event_type", 1)],
        unique=True,
        partialFilterExpression={"execution_nonce": {"$exists": True}},
        name="uniq_apm_feedback_event",
    )
    await db["apm_events"].create_index(
        [("user_id", 1), ("recorded_at", -1)], name="idx_apm_event_timeline"
    )
    await db["apm_episodes"].create_index(
        [("user_id", 1), ("episode_id", 1)], unique=True, name="uniq_apm_episode"
    )
    await db["apm_episodes"].create_index(
        [("user_id", 1), ("execution_nonce", 1)],
        unique=True,
        name="uniq_apm_episode_nonce",
    )


async def personalization_enabled(
    db: AsyncIOMotorDatabase, user_id: str
) -> bool:
    user = await db["users"].find_one(
        {"user_id": user_id}, {"personalization_consent": 1, "isActive": 1}
    )
    return bool(
        user
        and user.get("isActive", True)
        and user.get("personalization_consent", False)
    )


async def _upsert_node(
    db: AsyncIOMotorDatabase,
    user_id: str,
    node_type: APMNodeType,
    label: str,
    *,
    aliases: Iterable[str] = (),
    confidence_score: float,
    valence: Optional[float] = None,
    arousal: Optional[float] = None,
    intensity: Optional[float] = None,
    bucket: str,
    bootstrapped: bool = False,
    source_kind: str = "inferred",
    message: str = "",
) -> str:
    if _blocked_psychological_label(label):
        return ""
    node_id = make_apm_node_id(user_id, node_type, label)
    now = datetime.now(timezone.utc)
    existing = await db["apm_nodes"].find_one(
        {"user_id": user_id, "node_id": node_id},
        {"status": 1},
    )
    if (
        existing
        and existing.get("status") == _STATUS_INVALIDATED
        and source_kind != "explicit"
    ):
        return node_id
    from services.security import seal_text

    clean_aliases = {
        _canonical_label(alias)
        for alias in aliases
        if alias and alias.strip()
    }
    clean_aliases.add(_canonical_label(label))
    intensity_value = intensity
    if intensity_value is None and arousal is not None:
        intensity_value = arousal
    status = _STATUS_ACTIVE
    await db["apm_nodes"].update_one(
        {"user_id": user_id, "node_id": node_id},
        {
            "$set": {
                "user_id": user_id,
                "node_id": node_id,
                "node_type": node_type.value,
                "canonical_label": _canonical_label(label),
                "display_label": seal_text(label.strip()),
                "last_seen_at": now,
                "attributes.valence": valence,
                "attributes.arousal": arousal,
                "attributes.intensity": intensity_value,
                "source_kind": source_kind,
                "status": status,
                "bootstrapped": bootstrapped,
            },
            "$max": {"confidence_score": confidence_score},
            "$inc": {"occurrence_count": 1},
            "$addToSet": {
                "aliases": {"$each": sorted(clean_aliases)},
                "temporal_buckets": bucket,
            },
            "$setOnInsert": {"first_seen_at": now},
        },
        upsert=True,
    )
    register_metric("apm_pattern_update")
    return node_id


async def _upsert_transition(
    db: AsyncIOMotorDatabase,
    user_id: str,
    transition: APMTransition,
    *,
    bucket: str,
    bootstrapped: bool = False,
) -> str:
    source_id = make_apm_node_id(
        user_id, transition.source_type, transition.source_label
    )
    target_id = make_apm_node_id(
        user_id, transition.target_type, transition.target_label
    )
    edge_id = make_apm_edge_id(
        user_id, source_id, transition.relation_type, target_id
    )
    now = datetime.now(timezone.utc)
    await db["apm_edges"].update_one(
        {"user_id": user_id, "edge_id": edge_id},
        {
            "$set": {
                "user_id": user_id,
                "edge_id": edge_id,
                "source_node_id": source_id,
                "target_node_id": target_id,
                "relation_type": transition.relation_type.value,
                "decay_rate": decay_rate(transition.relation_type),
                "updated_at": now,
                "bootstrapped": bootstrapped,
            },
            "$max": {"confidence_score": transition.confidence_score},
            "$inc": {"observation_count": 1, "version": 1},
            "$addToSet": {"temporal_buckets": bucket},
            "$setOnInsert": {
                "created_at": now,
                "execution_count": 0,
                "completion_count": 0,
                "explicit_successes": 0,
                "explicit_failures": 0,
                "bayesian_score": 0.5,
            },
        },
        upsert=True,
    )
    return edge_id


async def persist_apm_extraction(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
    extraction: APMExtraction,
    *,
    message: str = "",
    source_message_id: str = "",
) -> int:
    """Persist one minimized temporal observation when consent permits."""
    if extraction.crisis_signal_detected:
        register_metric("apm_suppression")
        return 0
    if contains_crisis_signal(message):
        register_metric("apm_suppression")
        return 0
    if not await personalization_enabled(db, user_id):
        register_metric("apm_suppression")
        return 0

    corrected = await apply_user_correction(db, user_id, message)
    if corrected:
        register_metric("apm_correction")

    nonce = make_observation_nonce(user_id, session_id, message)
    existing_event = await db["apm_events"].find_one(
        {
            "user_id": user_id,
            "execution_nonce": nonce,
            "event_type": "OBSERVATION",
        }
    )
    if existing_event:
        return 0

    bucket = temporal_bucket()
    node_ids: Dict[tuple[str, str], str] = {}
    for observation in extraction.observations:
        source_kind = (observation.evidence_kind or "").casefold()
        if source_kind not in {"explicit", "inferred"}:
            source_kind = (
                "explicit" if labels_overlap(message, observation.label) else "inferred"
            )
        node_id = await _upsert_node(
            db,
            user_id,
            observation.node_type,
            observation.label,
            aliases=observation.aliases,
            confidence_score=observation.confidence_score,
            valence=observation.valence,
            arousal=observation.arousal,
            intensity=observation.intensity,
            bucket=bucket,
            source_kind=source_kind,
            message=message,
        )
        if not node_id:
            continue
        node_ids[(observation.node_type.value, _canonical_label(observation.label))] = (
            node_id
        )

    edge_ids = []
    for transition in extraction.transitions:
        for node_type, label in (
            (transition.source_type, transition.source_label),
            (transition.target_type, transition.target_label),
        ):
            key = (node_type.value, _canonical_label(label))
            if key not in node_ids:
                node_ids[key] = await _upsert_node(
                    db,
                    user_id,
                    node_type,
                    label,
                    confidence_score=transition.confidence_score,
                    bucket=bucket,
                    source_kind="inferred",
                    message=message,
                )
            if not node_ids.get(key):
                node_ids.pop(key, None)
        if not node_ids.get(
            (transition.source_type.value, _canonical_label(transition.source_label))
        ) or not node_ids.get(
            (transition.target_type.value, _canonical_label(transition.target_label))
        ):
            continue
        edge_ids.append(
            await _upsert_transition(db, user_id, transition, bucket=bucket)
        )

    episode_id = await _persist_episode(
        db,
        user_id=user_id,
        session_id=session_id,
        nonce=nonce,
        extraction=extraction,
        node_ids=node_ids,
        message=message,
        source_message_id=source_message_id,
    )

    try:
        await db["apm_events"].insert_one(
            {
                "user_id": user_id,
                "session_id": session_id,
                "event_type": "OBSERVATION",
                "execution_nonce": nonce,
                "episode_id": episode_id,
                "node_ids": [item for item in node_ids.values() if item],
                "edge_ids": edge_ids,
                "source_message_id": source_message_id,
                "temporal_bucket": bucket,
                "recorded_at": datetime.now(timezone.utc),
            }
        )
    except DuplicateKeyError:
        return 0
    register_metric("apm_extraction")
    logger.info(
        "apm_extraction user=%s nodes=%s edges=%s episode=%s",
        hash_user_id(user_id),
        len(node_ids),
        len(edge_ids),
        bool(episode_id),
    )
    return len([item for item in node_ids.values() if item]) + len(edge_ids)


async def _persist_episode(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    nonce: str,
    extraction: APMExtraction,
    node_ids: Dict[tuple[str, str], str],
    message: str,
    source_message_id: str,
) -> str:
    def _first(node_type: APMNodeType) -> str:
        for observation in extraction.observations:
            if observation.node_type is node_type:
                return node_ids.get(
                    (node_type.value, _canonical_label(observation.label)), ""
                )
        return ""

    context_label = ""
    valence = None
    intensity = None
    confidence = 0.0
    explicitness = "inferred"
    for observation in extraction.observations:
        if observation.node_type is APMNodeType.CONTEXT and not context_label:
            context_label = normalize_context_bucket(observation.label)
        if observation.valence is not None and valence is None:
            valence = observation.valence
        intensity_value = observation.intensity
        if intensity_value is None:
            intensity_value = observation.arousal
        if intensity_value is not None and intensity is None:
            intensity = intensity_value
        confidence = max(confidence, float(observation.confidence_score or 0))
        kind = (observation.evidence_kind or "").casefold()
        if kind == "explicit" or labels_overlap(message, observation.label):
            explicitness = "explicit"
    if not context_label:
        context_label = normalize_context_bucket(message)
    episode_id = f"apmep_{_owner_namespace(user_id)}__{nonce}"
    source_hash = sha256((message or "").encode("utf-8")).hexdigest()[:16]
    try:
        await db["apm_episodes"].insert_one(
            {
                "episode_id": episode_id,
                "user_id": user_id,
                "execution_nonce": nonce,
                "timestamp": datetime.now(timezone.utc),
                "context": context_label,
                "trigger_node_id": _first(APMNodeType.TRIGGER),
                "latent_state_node_id": _first(APMNodeType.LATENT_STATE),
                "intervention_node_id": _first(APMNodeType.INTERVENTION),
                "outcome_node_id": _first(APMNodeType.OUTCOME),
                "valence": valence,
                "intensity": intensity,
                "explicitness": explicitness,
                "confidence": round(confidence, 3),
                "source_session_id": session_id,
                "source_message_id": source_message_id,
                "source_reference": {
                    "kind": "chat_turn",
                    "session_id": session_id,
                    "content_hash": source_hash,
                },
                "status": _STATUS_ACTIVE,
            }
        )
    except DuplicateKeyError:
        return episode_id
    return episode_id


async def apply_user_correction(
    db: AsyncIOMotorDatabase, user_id: str, message: str
) -> int:
    """Invalidate overlapping active nodes when the user denies the memory."""
    if not user_id or not any(marker in (message or "").casefold() for marker in _CORRECTION_MARKERS):
        return 0
    cursor = db["apm_nodes"].find(
        {"user_id": user_id, "status": {"$ne": _STATUS_INVALIDATED}}
    )
    nodes = await cursor.to_list(length=40)
    updated = 0
    now = datetime.now(timezone.utc)
    for node in nodes:
        if node.get("user_id") != user_id:
            continue
        label = _open_label(node.get("display_label")) or str(
            node.get("canonical_label") or ""
        )
        if not message_contradicts_topic(message, label) and not labels_overlap(
            message, label
        ):
            continue
        if not labels_overlap(message, label):
            continue
        await db["apm_nodes"].update_one(
            {"user_id": user_id, "node_id": node["node_id"]},
            {
                "$set": {
                    "status": _STATUS_INVALIDATED,
                    "updated_at": now,
                    "confidence_score": min(
                        float(node.get("confidence_score") or 0) * 0.3, 0.2
                    ),
                }
            },
        )
        await db["apm_episodes"].update_many(
            {
                "user_id": user_id,
                "$or": [
                    {"trigger_node_id": node["node_id"]},
                    {"latent_state_node_id": node["node_id"]},
                ],
            },
            {"$set": {"status": _STATUS_INVALIDATED}},
        )
        updated += 1
    if updated:
        await db["apm_events"].insert_one(
            {
                "user_id": user_id,
                "event_type": "CORRECTION",
                "updated_count": updated,
                "recorded_at": now,
            }
        )
    return updated


async def get_relevant_associations(
    db: AsyncIOMotorDatabase,
    user_id: str,
    message: str,
    *,
    limit: int = 4,
) -> List[Dict[str, Any]]:
    """Bounded trigger/state associations for later-turn response shaping."""
    if contains_crisis_signal(message) or not await personalization_enabled(db, user_id):
        return []
    words = _message_tokens(message)
    if not words:
        return []
    query: Dict[str, Any] = {
        "user_id": user_id,
        "status": {"$ne": _STATUS_INVALIDATED},
        "node_type": {
            "$in": [
                APMNodeType.TRIGGER.value,
                APMNodeType.LATENT_STATE.value,
                APMNodeType.CONTEXT.value,
            ]
        },
    }
    if words:
        pattern = "|".join(re.escape(word) for word in sorted(words))
        query["$or"] = [
            {"canonical_label": {"$regex": pattern, "$options": "i"}},
            {"aliases": {"$regex": pattern, "$options": "i"}},
        ]
    cursor = db["apm_nodes"].find(query).sort("last_seen_at", -1).limit(8)
    nodes = await _cursor_list(cursor, 8)
    trigger_ids = [
        node["node_id"]
        for node in nodes
        if node.get("node_type") == APMNodeType.TRIGGER.value
    ]
    edges = []
    if trigger_ids:
        edge_cursor = (
            db["apm_edges"]
            .find(
                {
                    "user_id": user_id,
                    "source_node_id": {"$in": trigger_ids},
                    "relation_type": APMRelationType.TRIGGERS.value,
                }
            )
            .limit(12)
        )
        edges = await _cursor_list(edge_cursor, 12)
    target_ids = [edge["target_node_id"] for edge in edges]
    targets: Dict[str, Dict[str, Any]] = {}
    if target_ids:
        target_cursor = db["apm_nodes"].find(
            {
                "user_id": user_id,
                "node_id": {"$in": target_ids},
                "status": {"$ne": _STATUS_INVALIDATED},
            }
        )
        targets = {
            node["node_id"]: node for node in await _cursor_list(target_cursor, len(target_ids))
        }
    sources = {node["node_id"]: node for node in nodes}
    associations = []
    used = set()
    for edge in edges:
        source = sources.get(edge["source_node_id"])
        target = targets.get(edge["target_node_id"])
        if not source or not target:
            continue
        source_label = _open_label(source.get("display_label")) or str(
            source.get("canonical_label") or ""
        )
        target_label = _open_label(target.get("display_label")) or str(
            target.get("canonical_label") or ""
        )
        if message_contradicts_topic(message, source_label):
            continue
        associations.append(
            {
                "node_id": source["node_id"],
                "node_type": "TRIGGER",
                "label": f"{source_label} associated with {target_label}",
                "valence": (source.get("attributes") or {}).get("valence"),
                "intensity": (source.get("attributes") or {}).get("intensity"),
                "occurrence_count": int(source.get("occurrence_count") or 1),
                "evidence_kind": source.get("source_kind") or "inferred",
                "recurrence_confidence": recurrence_confidence(
                    int(source.get("occurrence_count") or 1)
                ),
            }
        )
        used.add(source["node_id"])
        if len(associations) >= limit:
            return associations
    for node in nodes:
        if node.get("user_id") != user_id or node["node_id"] in used:
            continue
        label = _open_label(node.get("display_label")) or str(
            node.get("canonical_label") or ""
        )
        if message_contradicts_topic(message, label):
            continue
        associations.append(
            {
                "node_id": node["node_id"],
                "node_type": node.get("node_type"),
                "label": label,
                "valence": (node.get("attributes") or {}).get("valence"),
                "intensity": (node.get("attributes") or {}).get("intensity"),
                "occurrence_count": int(node.get("occurrence_count") or 1),
                "evidence_kind": node.get("source_kind") or "inferred",
                "recurrence_confidence": recurrence_confidence(
                    int(node.get("occurrence_count") or 1)
                ),
            }
        )
        if len(associations) >= limit:
            break
    return associations


def effective_edge_score(edge: Dict[str, Any], at: Optional[datetime] = None) -> float:
    now = at or datetime.now(timezone.utc)
    updated = edge.get("last_success_at") or edge.get("updated_at") or now
    if getattr(updated, "tzinfo", None) is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - updated).total_seconds() / 86_400)
    base = float(edge.get("bayesian_score", 0.5))
    decayed = base * math.exp(-float(edge.get("decay_rate", 0.01)) * age_days)
    if edge.get("last_outcome") == "FAILURE":
        decayed -= get_settings().APM_FAILURE_PENALTY
    return max(0.0, min(1.0, decayed))


async def record_intervention_feedback(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    edge_id: str,
    intervention_id: str,
    execution_nonce: str,
    event_type: str,
    before_state: Optional[float] = None,
    after_state: Optional[float] = None,
) -> bool:
    """Record feedback once and atomically update its owned recovery edge."""
    event_type = event_type.upper()
    if event_type not in _FEEDBACK_EVENTS:
        raise ValueError(f"Unsupported feedback event: {event_type}")
    _assert_owned_id(user_id, edge_id, "apme")
    _assert_owned_id(user_id, intervention_id, "apm")
    if not await personalization_enabled(db, user_id):
        raise PermissionError("personalization consent is required")

    edge = await db["apm_edges"].find_one(
        {
            "user_id": user_id,
            "edge_id": edge_id,
            "target_node_id": intervention_id,
            "relation_type": APMRelationType.RECOVERED_BY.value,
        },
        {"_id": 1},
    )
    if not edge:
        raise ValueError("recovery edge not found for authenticated user")

    now = datetime.now(timezone.utc)
    event_key = {
        "user_id": user_id,
        "execution_nonce": execution_nonce,
        "event_type": event_type,
    }
    try:
        await db["apm_events"].insert_one(
            {
                **event_key,
                "edge_id": edge_id,
                "intervention_id": intervention_id,
                "before_state": before_state,
                "after_state": after_state,
                "recorded_at": now,
                "applied": False,
            }
        )
    except DuplicateKeyError:
        pass

    # Claim this event before applying it. A crashed worker leaves `applying`
    # set; operational recovery can clear stale claims, while normal retries
    # cannot double-increment the edge.
    claimed = await db["apm_events"].find_one_and_update(
        {
            **event_key,
            "applied": {"$ne": True},
            "applying": {"$ne": True},
        },
        {"$set": {"applying": True, "apply_started_at": now}},
    )
    if not claimed:
        return False

    success = event_type == "HELPFUL"
    failure = event_type == "NOT_HELPFUL"
    execution_inc = 1 if event_type == "STARTED" else 0
    completion_inc = 1 if event_type == "COMPLETED" else 0
    success_inc = 1 if success else 0
    failure_inc = 1 if failure else 0
    last_outcome = "SUCCESS" if success else "FAILURE" if failure else None

    first_stage: Dict[str, Any] = {
        "execution_count": {
            "$add": [{"$ifNull": ["$execution_count", 0]}, execution_inc]
        },
        "completion_count": {
            "$add": [{"$ifNull": ["$completion_count", 0]}, completion_inc]
        },
        "explicit_successes": {
            "$add": [{"$ifNull": ["$explicit_successes", 0]}, success_inc]
        },
        "explicit_failures": {
            "$add": [{"$ifNull": ["$explicit_failures", 0]}, failure_inc]
        },
        "version": {"$add": [{"$ifNull": ["$version", 0]}, 1]},
        "updated_at": now,
    }
    if success:
        first_stage["last_success_at"] = now
    if last_outcome:
        first_stage["last_outcome"] = last_outcome
        first_stage["last_outcome_at"] = now

    try:
        await db["apm_edges"].update_one(
            {"user_id": user_id, "edge_id": edge_id},
            [
                {"$set": first_stage},
                {
                    "$set": {
                        "bayesian_score": {
                            "$divide": [
                                {"$add": ["$explicit_successes", 2]},
                                {
                                    "$add": [
                                        "$explicit_successes",
                                        "$explicit_failures",
                                        4,
                                    ]
                                },
                            ]
                        }
                    }
                },
            ],
        )
        await db["apm_events"].update_one(
            event_key,
            {
                "$set": {"applied": True, "applied_at": now},
                "$unset": {"applying": "", "apply_started_at": ""},
            },
        )
    except Exception:
        logger.exception("APM event apply failed; clearing applying lock")
        await db["apm_events"].update_one(
            event_key,
            {"$unset": {"applying": "", "apply_started_at": ""}},
        )
        raise
    if event_type in {"HELPFUL", "NOT_HELPFUL"}:
        register_metric("apm_outcome_update")
    return True


async def _cursor_list(cursor: Any, length: int) -> List[Dict[str, Any]]:
    return await cursor.to_list(length=length)


async def get_recovery_paths(
    db: AsyncIOMotorDatabase,
    user_id: str,
    message: str,
    *,
    limit: int = 3,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Return bounded recovery evidence; inferred matches are background-only."""
    if contains_crisis_signal(message):
        return []
    if not await personalization_enabled(db, user_id):
        return []

    now = now or datetime.now(timezone.utc)
    bucket = temporal_bucket(now)
    words = {
        word
        for word in re.findall(r"[\w']+", (message or "").casefold())
        if len(word) >= 3
    }
    direct = bool(words)
    nodes: List[Dict[str, Any]] = []
    if words:
        pattern = "|".join(re.escape(word) for word in sorted(words))
        cursor = (
            db["apm_nodes"]
            .find(
                {
                    "user_id": user_id,
                    "status": {"$ne": _STATUS_INVALIDATED},
                    "node_type": {
                        "$in": [
                            APMNodeType.TRIGGER.value,
                            APMNodeType.LATENT_STATE.value,
                            APMNodeType.CONTEXT.value,
                        ]
                    },
                    "$or": [
                        {"canonical_label": {"$regex": pattern, "$options": "i"}},
                        {"aliases": {"$regex": pattern, "$options": "i"}},
                    ],
                }
            )
            .sort("last_seen_at", -1)
            .limit(8)
        )
        nodes = await _cursor_list(cursor, 8)

    if not nodes:
        direct = False
        cursor = (
            db["apm_nodes"]
            .find(
                {
                    "user_id": user_id,
                    "status": {"$ne": _STATUS_INVALIDATED},
                    "node_type": APMNodeType.LATENT_STATE.value,
                    "temporal_buckets": bucket,
                }
            )
            .sort("last_seen_at", -1)
            .limit(5)
        )
        nodes = await _cursor_list(cursor, 5)
    if not nodes:
        cursor = (
            db["apm_nodes"]
            .find(
                {
                    "user_id": user_id,
                    "status": {"$ne": _STATUS_INVALIDATED},
                    "node_type": APMNodeType.LATENT_STATE.value,
                }
            )
            .sort("last_seen_at", -1)
            .limit(5)
        )
        nodes = await _cursor_list(cursor, 5)
    if not nodes:
        return []

    node_ids = [node["node_id"] for node in nodes]
    threshold = get_settings().APM_RECOMMENDATION_CONFIDENCE
    edge_cursor = (
        db["apm_edges"]
        .find(
            {
                "user_id": user_id,
                "source_node_id": {"$in": node_ids},
                "relation_type": APMRelationType.RECOVERED_BY.value,
            }
        )
        .sort("bayesian_score", -1)
        .limit(12)
    )
    edges = await _cursor_list(edge_cursor, 12)
    if not edges:
        return []

    target_ids = [edge["target_node_id"] for edge in edges]
    target_cursor = db["apm_nodes"].find(
        {
            "user_id": user_id,
            "node_id": {"$in": target_ids},
            "node_type": APMNodeType.INTERVENTION.value,
        }
    )
    targets = {
        node["node_id"]: node for node in await _cursor_list(target_cursor, len(target_ids))
    }
    sources = {node["node_id"]: node for node in nodes}

    paths = []
    for edge in edges:
        source = sources.get(edge["source_node_id"])
        target = targets.get(edge["target_node_id"])
        if not source or not target:
            continue
        source_label = _open_label(source.get("display_label")) or str(
            source.get("canonical_label") or ""
        )
        intervention_label = _open_label(target.get("display_label")) or str(
            target.get("canonical_label") or ""
        )
        if message_contradicts_topic(message, source_label) or message_contradicts_topic(
            message, intervention_label
        ):
            continue
        score = effective_edge_score(edge, now)
        eligible = bool(
            direct
            and source.get("confidence_score", 0) >= threshold
            and target.get("confidence_score", 0) >= threshold
            and edge.get("confidence_score", 0) >= threshold
            and edge.get("explicit_successes", 0) >= 1
            and edge.get("last_outcome") != "FAILURE"
        )
        paths.append(
            {
                "edge_id": edge["edge_id"],
                "source_node_id": source["node_id"],
                "source_label": source_label,
                "intervention_id": target["node_id"],
                "intervention_label": intervention_label,
                "effective_score": round(score, 3),
                "confidence_score": min(
                    source.get("confidence_score", 0),
                    target.get("confidence_score", 0),
                    edge.get("confidence_score", 0),
                ),
                "inferred": not direct,
                "recommendation_eligible": eligible,
            }
        )
    paths.sort(key=lambda item: item["effective_score"], reverse=True)
    return paths[:limit]


def format_adaptive_memory_context(
    paths: List[Dict[str, Any]],
    associations: Optional[List[Dict[str, Any]]] = None,
) -> str:
    associations = associations or []
    if not paths and not associations:
        return EMPTY_APM_CONTEXT
    lines = [
        "Tentative user-specific psychological memory. Never recite scores, "
        "ids, or diagnoses to the user. Associated-with, not caused-by. "
        "Current conversation can override stale memory."
    ]
    for item in associations:
        lines.append(
            f"- Association: {item.get('label')} ({item.get('node_type')}, "
            f"evidence={item.get('evidence_kind')}, "
            f"recurrence={item.get('occurrence_count')})"
        )
    for path in paths:
        mode = "eligible_for_one_card" if path["recommendation_eligible"] else "background_only"
        lines.append(
            f"- State/trigger: {path['source_label']} -> intervention: "
            f"{path['intervention_label']} ({mode}, inferred={path['inferred']}, "
            f"score={path['effective_score']}, edge_id={path['edge_id']}, "
            f"intervention_id={path['intervention_id']})"
        )
    return "\n".join(lines)


async def get_adaptive_memory_context(
    db: AsyncIOMotorDatabase, user_id: str, message: str
) -> str:
    try:
        register_metric("apm_retrieval")
        associations = await get_relevant_associations(db, user_id, message)
        paths = await get_recovery_paths(db, user_id, message)
        return format_adaptive_memory_context(paths, associations)
    except Exception:
        # APM is additive and must never block a conversation.
        logger.exception("APM context failed user=%s", user_id)
        return EMPTY_APM_CONTEXT


async def delete_adaptive_memory(
    db: AsyncIOMotorDatabase, user_id: str
) -> Dict[str, int]:
    """Delete all APM material for one authenticated user."""
    register_metric("apm_erasure")
    deleted: Dict[str, int] = {}
    for collection in ("apm_nodes", "apm_edges", "apm_events", "apm_episodes"):
        result = await db[collection].delete_many({"user_id": user_id})
        deleted[collection] = result.deleted_count
    return deleted


async def evaluate_jitai_candidate(
    db: AsyncIOMotorDatabase, user_id: str, message: str = ""
) -> Dict[str, Any]:
    """
    Internal timing signal only. Outbound delivery is intentionally disabled.
    """
    if not await personalization_enabled(db, user_id):
        return {
            "candidate_detected": False,
            "outbound_enabled": False,
            "reason": "consent_required",
        }
    paths = await get_recovery_paths(db, user_id, message)
    return {
        "candidate_detected": bool(paths),
        "outbound_enabled": False,
        "temporal_bucket": temporal_bucket(),
        "reason": "outbound_jitai_not_released",
    }


async def bootstrap_existing_graph(
    db: AsyncIOMotorDatabase, user_id: str, *, confidence_score: float = 0.25
) -> int:
    """Map legacy graph facts into background-only APM memory."""
    if confidence_score >= get_settings().APM_RECOMMENDATION_CONFIDENCE:
        raise ValueError("bootstrapped confidence must remain below card threshold")

    type_map = {
        "Trigger": APMNodeType.TRIGGER,
        "Emotion": APMNodeType.LATENT_STATE,
        "CopingTool": APMNodeType.INTERVENTION,
    }
    cursor = db["graph_nodes"].find(
        {"user_id": user_id, "node_type": {"$in": list(type_map)}}
    )
    legacy_nodes = await cursor.to_list(length=500)
    by_legacy_id = {node["node_id"]: node for node in legacy_nodes}
    bucket = temporal_bucket()
    count = 0
    for node in legacy_nodes:
        await _upsert_node(
            db,
            user_id,
            type_map[node["node_type"]],
            node.get("name") or node["node_id"],
            confidence_score=confidence_score,
            bucket=bucket,
            bootstrapped=True,
        )
        count += 1

    rel_cursor = db["graph_relationships"].find(
        {
            "user_id": user_id,
            "relation": {"$in": ["TRIGGERED_BY", "HELPED_WITH"]},
        }
    )
    for rel in await rel_cursor.to_list(length=1000):
        source = by_legacy_id.get(rel.get("from_node_id"))
        target = by_legacy_id.get(rel.get("to_node_id"))
        if not source or not target:
            continue
        transition = None
        if (
            rel["relation"] == "TRIGGERED_BY"
            and source["node_type"] == "Emotion"
            and target["node_type"] == "Trigger"
        ):
            transition = APMTransition(
                source_type=APMNodeType.TRIGGER,
                source_label=target["name"],
                target_type=APMNodeType.LATENT_STATE,
                target_label=source["name"],
                relation_type=APMRelationType.TRIGGERS,
                confidence_score=confidence_score,
            )
        elif (
            rel["relation"] == "HELPED_WITH"
            and source["node_type"] == "CopingTool"
            and target["node_type"] == "Emotion"
        ):
            transition = APMTransition(
                source_type=APMNodeType.LATENT_STATE,
                source_label=target["name"],
                target_type=APMNodeType.INTERVENTION,
                target_label=source["name"],
                relation_type=APMRelationType.RECOVERED_BY,
                confidence_score=confidence_score,
            )
        if transition:
            await _upsert_transition(
                db,
                user_id,
                transition,
                bucket=bucket,
                bootstrapped=True,
            )
            count += 1
    return count

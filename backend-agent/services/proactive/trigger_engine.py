"""Bounded, user-scoped APM/graph retrieval, decay, and divergence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import re

from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import get_settings
from schemas import APMNodeType
from services.apm import (
    contains_crisis_signal,
    get_recovery_paths,
    message_contradicts_topic,
)
from services.proactive.receptivity import appears_minimizing
from services.proactive.schemas import DivergenceResult, GraphSnippet
from services.safety_class import SafetyClass, classify_message
from backend_core.security import open_text

_ACADEMIC = (
    "exam",
    "test",
    "assignment",
    "homework",
    "presentation",
    "project",
    "marks",
    "grade",
    "school",
    "study",
    "studying",
)
_SLEEP = ("sleep", "slept", "insomnia", "tired", "rest", "night")
_HABIT = ("habit", "practice", "routine", "streak")
_STRESS = (
    "stress",
    "stressed",
    "overwhelm",
    "heavy",
    "pressure",
    "deadline",
    "workload",
)

_EMPTY_SLEEP = "No sleep data available"
_EMPTY_JOURNAL = "No journal entries available."
_EMPTY_HABIT = "No active habits tracked."


def _safe_label(raw: str) -> str:
    label = " ".join((raw or "").split())
    lowered = label.casefold()
    blocked = (
        "anxiety",
        "depression",
        "adhd",
        "ptsd",
        "bipolar",
        "suicide",
        "self-harm",
        "self harm",
    )
    if any(term in lowered for term in blocked):
        return ""
    if classify_message(label) is not SafetyClass.NONE:
        return ""
    if contains_crisis_signal(label):
        return ""
    if len(label) > 48:
        label = label[:47].rstrip() + "…"
    return label


def _age_days(stamp: Any, now: datetime) -> float:
    if stamp is None:
        return 10_000.0
    if getattr(stamp, "tzinfo", None) is None and isinstance(stamp, datetime):
        stamp = stamp.replace(tzinfo=timezone.utc)
    if not isinstance(stamp, datetime):
        return 10_000.0
    return max(0.0, (now - stamp).total_seconds() / 86_400)


def _relevance(node: Dict[str, Any], now: datetime) -> float:
    settings = get_settings()
    age = _age_days(node.get("last_seen_at") or node.get("updated_at"), now)
    if age > float(settings.PROACTIVE_LOOKBACK_DAYS):
        return 0.0
    confidence = float(node.get("confidence_score") or 0.0)
    recency = max(0.0, 1.0 - (age / float(settings.PROACTIVE_LOOKBACK_DAYS)))
    return max(0.0, min(1.0, confidence * recency))


def _message_tokens(message: str) -> set[str]:
    return {
        token
        for token in "".join(
            ch.lower() if ch.isalnum() else " " for ch in (message or "")
        ).split()
        if len(token) >= 3
    }


def current_turn_overlaps_topic(message: str, topic: str) -> bool:
    topic_tokens = _message_tokens(topic)
    if not topic_tokens:
        return False
    words = _message_tokens(message)
    return bool(topic_tokens & words)


_EMPTY_APM = "No adaptive psychological memory available."
_EMPTY_GRAPH = "No relational graph data available yet."
_APM_LINE = re.compile(
    r"State/trigger:\s*(.+?)\s*->\s*intervention:\s*(.+?)\s*\(",
    re.IGNORECASE,
)
_GRAPH_LINE = re.compile(
    r"(?:Trigger|Life event|Emotional state):\s*(.+?)(?:\s+[—\-]|$)",
    re.IGNORECASE,
)


def existing_apm_usable(adaptive_memory_context: str) -> bool:
    body = adaptive_memory_context or ""
    return bool(body) and _EMPTY_APM not in body and "State/trigger:" in body


def existing_graph_usable(graph_context: str) -> bool:
    body = graph_context or ""
    return bool(body) and _EMPTY_GRAPH not in body


def hydrate_from_existing_context(
    *,
    graph_context: str = "",
    adaptive_memory_context: str = "",
) -> Dict[str, Any]:
    """Pull labels from context the chat turn already fetched. No extra query."""
    snippets: List[GraphSnippet] = []
    recovery: List[Dict[str, Any]] = []
    for match in _APM_LINE.finditer(adaptive_memory_context or ""):
        source = _safe_label(match.group(1))
        intervention = _safe_label(match.group(2))
        if source:
            snippets.append(
                GraphSnippet(
                    node_type="TRIGGER",
                    label=source,
                    confidence=0.55,
                )
            )
        if source and intervention:
            recovery.append(
                {
                    "source_label": source,
                    "intervention_label": intervention,
                    "effective_score": 0.55,
                    "inferred": False,
                    "source_node_id": "",
                }
            )
    for match in _GRAPH_LINE.finditer(graph_context or ""):
        label = _safe_label(match.group(1).split("—")[0].split("-")[0])
        if not label:
            continue
        if any(item.label.casefold() == label.casefold() for item in snippets):
            continue
        snippets.append(
            GraphSnippet(node_type="TRIGGER", label=label, confidence=0.5)
        )
    return {"nodes": snippets, "recovery": recovery}


async def retrieve_bounded_context(
    db: AsyncIOMotorDatabase,
    user_id: str,
    message: str,
    *,
    now: Optional[datetime] = None,
    graph_context: str = "",
    adaptive_memory_context: str = "",
) -> Dict[str, Any]:
    """Current context → recent trigger → latent state → pattern → outcome.

    Reuses already-fetched chat graph/APM strings when they can answer the
    decision. Extra user-scoped queries run only when those strings are empty.
    """
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    node_limit = int(settings.PROACTIVE_GRAPH_NODE_LIMIT)
    path_limit = int(settings.PROACTIVE_GRAPH_PATH_LIMIT)
    min_conf = float(settings.PROACTIVE_MIN_CONFIDENCE)

    apm_available = existing_apm_usable(adaptive_memory_context)
    graph_available = existing_graph_usable(graph_context)
    hydrated = hydrate_from_existing_context(
        graph_context=graph_context,
        adaptive_memory_context=adaptive_memory_context,
    )
    snippets: List[GraphSnippet] = list(hydrated.get("nodes") or [])
    recovery: List[Dict[str, Any]] = [
        path for path in (hydrated.get("recovery") or []) if not path.get("inferred")
    ][:path_limit]
    additional_apm = False
    additional_graph = False

    if not snippets:
        additional_apm = True
        nodes: List[Dict[str, Any]] = []
        try:
            cursor = (
                db["apm_nodes"]
                .find(
                    {
                        "user_id": user_id,
                        "node_type": {
                            "$in": [
                                APMNodeType.TRIGGER.value,
                                APMNodeType.LATENT_STATE.value,
                                APMNodeType.CONTEXT.value,
                                APMNodeType.INTERVENTION.value,
                                APMNodeType.OUTCOME.value,
                            ]
                        },
                    }
                )
                .sort("last_seen_at", -1)
                .limit(node_limit)
            )
            nodes = await cursor.to_list(length=node_limit)
        except Exception:
            nodes = []

        for node in nodes:
            if node.get("user_id") != user_id:
                continue
            if node.get("status") == "INVALIDATED":
                continue
            raw_label = open_text(node.get("display_label") or "") or str(
                node.get("canonical_label") or ""
            )
            label = _safe_label(str(raw_label))
            if not label:
                continue
            if message_contradicts_topic(message, label):
                continue
            score = _relevance(node, moment)
            if score < min_conf:
                continue
            snippets.append(
                GraphSnippet(
                    node_id=str(node.get("node_id") or ""),
                    node_type=str(node.get("node_type") or ""),
                    label=label,
                    confidence=score,
                    last_seen_at=node.get("last_seen_at"),
                )
            )

        if not recovery:
            try:
                recovery = await get_recovery_paths(
                    db, user_id, message or "", limit=path_limit, now=moment
                )
            except Exception:
                recovery = []
            recovery = [path for path in recovery if not path.get("inferred")][:path_limit]

    if not snippets and not graph_available:
        additional_graph = True
        snippets.extend(await _therapeutic_fallback(db, user_id, moment, min_conf, node_limit))

    snippets.sort(key=lambda item: item.confidence, reverse=True)
    return {
        "nodes": snippets[:node_limit],
        "edges": [],
        "recovery": recovery,
        "empty": not snippets and not recovery,
        "existing_graph_context_available": graph_available,
        "existing_apm_context_available": apm_available,
        "proactive_additional_graph_query": additional_graph,
        "proactive_additional_apm_query": additional_apm,
    }


async def _therapeutic_fallback(
    db: AsyncIOMotorDatabase,
    user_id: str,
    now: datetime,
    min_conf: float,
    limit: int,
) -> List[GraphSnippet]:
    extras: List[GraphSnippet] = []
    try:
        cursor = (
            db["graph_nodes"]
            .find(
                {
                    "user_id": user_id,
                    "node_type": {"$in": ["Trigger", "Emotion", "Event"]},
                }
            )
            .sort("updated_at", -1)
            .limit(limit)
        )
        rows = await cursor.to_list(length=limit)
    except Exception:
        return extras
    for row in rows:
        if row.get("user_id") != user_id:
            continue
        label = _safe_label(open_text(str(row.get("name") or "")))
        if not label:
            continue
        proxy = {
            "last_seen_at": row.get("updated_at"),
            "confidence_score": 0.55,
        }
        score = _relevance(proxy, now)
        if score < min_conf:
            continue
        extras.append(
            GraphSnippet(
                node_id=str(row.get("node_id") or ""),
                node_type=str(row.get("node_type") or ""),
                label=label,
                confidence=score,
                last_seen_at=row.get("updated_at"),
            )
        )
    return extras


def detect_divergence(
    message: str,
    bundle: Dict[str, Any],
    *,
    opening_turn: bool = False,
) -> DivergenceResult:
    """Mismatch between minimizing talk and recent owned history.

    Micro-signals are not passed in and cannot produce this result.
    """
    nodes: List[GraphSnippet] = list(bundle.get("nodes") or [])
    if not nodes:
        return DivergenceResult(detected=False, reason="empty_graph")
    topic_node = next(
        (
            node
            for node in nodes
            if node.node_type in {"TRIGGER", "CONTEXT", "Trigger", "Event"}
        ),
        nodes[0],
    )
    topic = topic_node.label
    if not topic:
        return DivergenceResult(detected=False, reason="no_safe_topic")
    if current_turn_overlaps_topic(message, topic) and not appears_minimizing(message):
        return DivergenceResult(
            detected=False,
            reason="compatible_current_topic",
            topic=topic,
            source_nodes=[topic_node],
        )
    if appears_minimizing(message) or (opening_turn and not (message or "").strip()):
        confidence = "moderate" if topic_node.confidence >= 0.5 else "low"
        return DivergenceResult(
            detected=True,
            confidence=confidence,
            reason="minimizing_vs_recent_pattern",
            topic=topic,
            source_nodes=[topic_node],
        )
    return DivergenceResult(
        detected=False, reason="no_divergence", topic=topic, source_nodes=[topic_node]
    )


def choose_trigger(
    message: str,
    bundle: Dict[str, Any],
    divergence: DivergenceResult,
    *,
    opening_turn: bool = False,
    journal_context: str = "",
    sleep_context: str = "",
    habit_context: str = "",
    academic_context: str = "",
) -> Tuple[str, GraphSnippet]:
    text = (message or "").casefold()
    nodes: List[GraphSnippet] = list(bundle.get("nodes") or [])
    recovery = list(bundle.get("recovery") or [])
    primary = divergence.source_nodes[0] if divergence.source_nodes else (
        nodes[0] if nodes else GraphSnippet()
    )

    if divergence.detected:
        return "GRAPH_DIVERGENCE", primary

    if recovery and (
        opening_turn or any(word in text for word in ("helped", "better", "tried", "again"))
    ):
        path = recovery[0]
        label = str(path.get("source_label") or primary.label)
        return "OUTCOME_FOLLOW_UP" if opening_turn else "RECOVERY_CHECK", GraphSnippet(
            node_id=str(path.get("source_node_id") or ""),
            node_type="LATENT_STATE",
            label=_safe_label(label) or "that situation",
            confidence=float(path.get("effective_score") or 0.4),
        )

    if opening_turn and primary.label:
        if any(token in primary.label.casefold() for token in _ACADEMIC):
            return "ACADEMIC_STRESS_CONTEXT", primary
        return "FOLLOW_UP_ON_PREVIOUS_CONTEXT", primary

    if _JOURNAL_HAS(journal_context) and (opening_turn or "journal" in text):
        return "JOURNAL_CONTEXT", primary
    if academic_context and academic_context != "No academic data available" and any(
        token in text for token in _ACADEMIC
    ):
        return "ACADEMIC_STRESS_CONTEXT", primary
    if sleep_context and sleep_context != _EMPTY_SLEEP and any(
        token in text for token in _SLEEP
    ):
        return "SLEEP_CONTEXT", primary
    if habit_context and habit_context != _EMPTY_HABIT and any(
        token in text for token in _HABIT
    ):
        return "HABIT_CONTEXT", primary
    if any(token in text for token in _STRESS) and primary.label:
        return "RECENT_STRESS_CONTEXT", primary
    if primary.label and not current_turn_overlaps_topic(message, primary.label):
        return "PATTERN_CLARIFICATION", primary
    return "", primary


def _JOURNAL_HAS(journal_context: str) -> bool:
    body = journal_context or ""
    return "RECENT JOURNAL CONTEXT" in body and _EMPTY_JOURNAL not in body

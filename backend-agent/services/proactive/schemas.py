"""Internal contracts for the proactive question engine.

Public HTTP models live in ``schemas.py``. Nothing here is returned to
the client except through ``public_payload()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional


class Decision(str, Enum):
    PROACTIVE_QUESTION = "PROACTIVE_QUESTION"
    NO_PROACTIVE_QUESTION = "NO_PROACTIVE_QUESTION"


class ProactiveStatus(str, Enum):
    CANDIDATE_CREATED = "CANDIDATE_CREATED"
    APPROVED = "APPROVED"
    DISPATCHED = "DISPATCHED"
    DELIVERED = "DELIVERED"
    SEEN = "SEEN"
    RESPONDED = "RESPONDED"
    SUPPRESSED = "SUPPRESSED"
    EXPIRED = "EXPIRED"


class ReceptivityState(str, Enum):
    RECEPTIVE = "RECEPTIVE"
    NEUTRAL = "NEUTRAL"
    LOW_RECEPTIVITY = "LOW_RECEPTIVITY"
    HIGH_OVERLOAD = "HIGH_OVERLOAD"
    UNKNOWN = "UNKNOWN"


class TriggerType(str, Enum):
    FOLLOW_UP_ON_PREVIOUS_CONTEXT = "FOLLOW_UP_ON_PREVIOUS_CONTEXT"
    PATTERN_CLARIFICATION = "PATTERN_CLARIFICATION"
    GRAPH_DIVERGENCE = "GRAPH_DIVERGENCE"
    RECENT_STRESS_CONTEXT = "RECENT_STRESS_CONTEXT"
    RECOVERY_CHECK = "RECOVERY_CHECK"
    ACADEMIC_STRESS_CONTEXT = "ACADEMIC_STRESS_CONTEXT"
    SLEEP_CONTEXT = "SLEEP_CONTEXT"
    HABIT_CONTEXT = "HABIT_CONTEXT"
    JOURNAL_CONTEXT = "JOURNAL_CONTEXT"
    OUTCOME_FOLLOW_UP = "OUTCOME_FOLLOW_UP"


# Signals named in the product strategy that are NOT on the telemetry
# allowlist and MUST NOT be collected or inferred by covert substitutes.
UNAVAILABLE_RECEPTIVITY_SIGNALS = (
    "typing_hesitation",
    "typing_speed",
    "typing_rhythm",
    "keystroke_content",
    "response_delay_ms",
    "app_open_count",
    "late_night_open_count",
    "return_frequency",
    "session_duration",
    "message_count",
    "microphone",
    "camera",
    "contacts",
    "location",
    "browser_history",
    "background_apps",
)


@dataclass
class GraphSnippet:
    node_id: str = ""
    node_type: str = ""
    label: str = ""
    confidence: float = 0.0
    last_seen_at: Optional[Any] = None
    relation: str = ""
    effective_score: float = 0.0


@dataclass
class DivergenceResult:
    detected: bool = False
    confidence: str = "low"
    reason: str = ""
    topic: str = ""
    source_nodes: List[GraphSnippet] = field(default_factory=list)


@dataclass
class QuestionCandidate:
    question: str
    reason: str
    trigger_type: str
    source_nodes: List[str] = field(default_factory=list)
    source_labels: List[str] = field(default_factory=list)
    confidence: float = 0.0
    receptivity: str = ReceptivityState.UNKNOWN.value
    risk_state: str = "none"
    language: str = "ENGLISH"
    script: str = "LATIN"
    topic: str = ""


@dataclass
class CouncilDecision:
    allowed: bool
    reason: str
    question: str = ""
    risk_check: str = ""
    empathy_check: str = ""
    reality_check: str = ""
    actionability_check: str = ""


@dataclass
class ProactiveResult:
    decision: str = Decision.NO_PROACTIVE_QUESTION.value
    reason: str = "not_evaluated"
    event_id: str = ""
    question: str = ""
    status: str = ""
    trigger_type: str = ""
    receptivity: str = ReceptivityState.UNKNOWN.value
    execution_nonce: str = ""
    confidence: float = 0.0
    language: str = "ENGLISH"
    script: str = "LATIN"
    source_node_ids: List[str] = field(default_factory=list)
    topic: str = ""
    existing_graph_context_available: bool = False
    existing_apm_context_available: bool = False
    proactive_additional_graph_query: bool = False
    proactive_additional_apm_query: bool = False
    proactive_retrieval_latency_ms: float = 0.0

    def public_payload(self) -> Dict[str, Any]:
        if self.decision == Decision.PROACTIVE_QUESTION.value:
            payload: Dict[str, Any] = {
                "decision": self.decision,
                "event_id": self.event_id or None,
                "question": self.question or None,
            }
            if self.status:
                payload["status"] = self.status
            return payload
        return {
            "decision": Decision.NO_PROACTIVE_QUESTION.value,
            "reason": self.reason or "suppressed",
        }


def no_question(reason: str, **kwargs: Any) -> ProactiveResult:
    base = ProactiveResult(
        decision=Decision.NO_PROACTIVE_QUESTION.value,
        reason=reason,
        status=ProactiveStatus.SUPPRESSED.value,
    )
    return replace(base, **kwargs) if kwargs else base

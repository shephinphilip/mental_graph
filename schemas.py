"""
schemas.py — Pydantic Data Models
===================================

Defines all shared data contracts used across the API layer, service
layer, and storage layer.  Using a single ``schemas.py`` module ensures
that the same validated types flow end-to-end without silent coercion or
data loss.

Module structure
----------------
Section 1 — Action Card Types
    ``CardType``       : Enum of supported action card flavours
    ``ActionCard``     : A single rendered action card surfaced in the chat UI

Section 2 — Chat API
    ``ChatMessageRequest``  : Incoming payload for POST /chat/send and /chat/stream
    ``ChatMessageResponse`` : Outgoing response from POST /chat/send

Section 3 — Downstream Extraction Pipeline
    ``SessionExtraction``  : LLM-extracted metadata from a conversation turn
                             (emotions, themes, crisis flags, insight summary)

Section 4 — Graph RAG Pipeline
    ``GraphNodeLabel``     : Enum of graph node label types
    ``GraphRelationType``  : Enum of graph relationship types
    ``GraphTuple``         : A single subject-predicate-object fact for MongoDB graph
    ``ExtractedGraphData`` : Container holding a list of ``GraphTuple`` objects

Section 5 — Session Resumption & Safety
    ``CrisisResource``         : Emergency helpline contact record
    ``SessionResumeResponse``  : Payload for GET /chat/session/{user_id}/resume
    ``StreamChunk``            : A single SSE event chunk for /chat/stream
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ════════════════════════════════════════════════════════════════════════════
# Section 1 — Action Card Types
# ════════════════════════════════════════════════════════════════════════════


class CardType(str, Enum):
    """
    Supported inline action card types rendered in the chat UI.

    Values
    ------
    TOOL    : Deep-link to an in-app wellness resource (body scan, breathing)
    HABIT   : One-tap habit creation pre-filled from the conversation context
    TASK    : One-tap task/action item creation
    BOOKING : Surface the professional therapy booking flow
    CONTENT : Recommended reading, listening, or video module
    """

    TOOL = "TOOL_CARD"
    HABIT = "HABIT_CARD"
    TASK = "TASK_CARD"
    BOOKING = "BOOKING_CARD"
    CONTENT = "CONTENT_CARD"


class ActionCard(BaseModel):
    """
    A structured action card surfaced inside the chat UI.

    Action cards are parsed from the LLM's raw output when it emits a
    ``<<<ACTION_CARD { ... } ACTION_CARD>>>`` block.  The cleaned
    conversational text (minus the card markup) is returned separately.

    Attributes
    ----------
    card_type : CardType
        Controls which UI component the front-end renders.
    title : str
        Short, user-facing heading displayed on the card.
    subtitle : str, optional
        Supporting description displayed below the title.
    action_payload : dict
        Arbitrary metadata the UI needs to execute the action.
        Schema depends on ``card_type`` — e.g. a TOOL_CARD might carry
        ``{"resource_id": "body_scan_8min"}``.
    """

    card_type: CardType
    title: str
    subtitle: Optional[str] = None
    card_id: Optional[str] = None
    cta_label: Optional[str] = None
    action_payload: Dict[str, Any] = Field(
        ...,
        description=(
            "Data required by the UI to execute the action "
            "(e.g., resource_id, habit_title, booking_flow_id)"
        ),
    )


# ════════════════════════════════════════════════════════════════════════════
# Section 2 — Chat API
# ════════════════════════════════════════════════════════════════════════════


class ChatMessageRequest(BaseModel):
    """
    Incoming chat message from the client application.

    Used by both POST /chat/send (JSON) and POST /chat/stream (SSE).

    Attributes
    ----------
    user_id : str
        Globally unique identifier for the user (e.g. Firebase UID).
        Used to load memory, mood logs, habits, and graph context.
    session_id : str
        Unique identifier for the current conversation session.
        Multiple sessions per user are supported.
    message : str
        The raw user message text.  PII anonymization is applied
        before this text is sent to external LLM APIs.
    """

    user_id: str
    session_id: str
    message: str


class ChatMessageResponse(BaseModel):
    """
    Outgoing chat response returned to the client (POST /chat/send).

    Attributes
    ----------
    session_id : str
        Echo of the session ID from the request.
    reply : str
        The AI companion's conversational reply, with action card markup
        stripped out.
    action_cards : list[ActionCard]
        Zero or more structured action cards parsed from the LLM output.
        Empty list if the LLM did not emit any cards in this turn.
    """

    session_id: str
    reply: str
    action_cards: List[ActionCard] = []


class LoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginResponse(BaseModel):
    user_id: str
    access_token: str
    token_type: str = "bearer"
    email: Optional[str] = None
    name: Optional[str] = None
    student_class: Optional[str] = None
    school: Optional[str] = None
    preferred_language: Optional[str] = None
    age: Optional[int] = None
    chief_concern: Optional[str] = None
    board: Optional[str] = None
    personalization_consent: bool = False


class WelcomeRequest(BaseModel):
    user_id: str
    session_id: str


class PersonalizationConsentRequest(BaseModel):
    enabled: bool


class APMFeedbackRequest(BaseModel):
    edge_id: str
    intervention_id: str
    execution_nonce: str
    event_type: str
    before_state: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    after_state: Optional[float] = Field(default=None, ge=-1.0, le=1.0)


class APMNodeType(str, Enum):
    TRIGGER = "TRIGGER"
    LATENT_STATE = "LATENT_STATE"
    INTERVENTION = "INTERVENTION"
    OUTCOME = "OUTCOME"
    CONTEXT = "CONTEXT"


class APMRelationType(str, Enum):
    TRIGGERS = "TRIGGERS"
    EVOLVES_INTO = "EVOLVES_INTO"
    RECOVERED_BY = "RECOVERED_BY"
    REINFORCES = "REINFORCES"


class APMObservation(BaseModel):
    node_type: APMNodeType
    label: str = Field(min_length=1, max_length=120)
    aliases: List[str] = Field(default_factory=list, max_length=8)
    valence: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    arousal: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)


class APMTransition(BaseModel):
    source_type: APMNodeType
    source_label: str = Field(min_length=1, max_length=120)
    target_type: APMNodeType
    target_label: str = Field(min_length=1, max_length=120)
    relation_type: APMRelationType
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)


class APMExtraction(BaseModel):
    observations: List[APMObservation] = Field(default_factory=list, max_length=12)
    transitions: List[APMTransition] = Field(default_factory=list, max_length=12)
    crisis_signal_detected: bool = False


# ════════════════════════════════════════════════════════════════════════════
# Section 3 — Downstream Extraction Pipeline
# ════════════════════════════════════════════════════════════════════════════


class SessionExtraction(BaseModel):
    """
    Structured metadata extracted from a conversation turn.

    Produced by the background extraction pipeline
    (``services/extraction.py``) and persisted to the
    ``user_insights`` MongoDB collection after each chat turn.

    Attributes
    ----------
    detected_emotions : list[str]
        Lowercase emotion labels identified in the user message
        (e.g. ``["anxious", "hopeful"]``).
    core_themes : list[str]
        Thematic tags relevant for downstream analytics and memory
        engines (e.g. ``["work_burnout", "relationship_conflict"]``).
    suggested_habits : list[str]
        Habit suggestions that emerged naturally from the conversation.
        These are candidates for a HABIT_CARD.
    crisis_signal_detected : bool
        ``True`` if the message contains self-harm ideation, suicidal
        thoughts, or severe acute distress.  Triggers the crisis
        escalation path in ``_handle_crisis_signal()``.
    escalation_recommended : bool
        ``True`` if the LLM believes the user needs professional support
        beyond the AI companion.  May surface a BOOKING_CARD.
    insight_summary : str
        A one-sentence narrative summary of what this conversation turn
        revealed about the user's emotional state.
    """

    detected_emotions: List[str] = Field(
        default_factory=list,
        description="Emotion labels detected in the user message (e.g., 'anxious', 'hopeful')",
    )
    core_themes: List[str] = Field(
        default_factory=list,
        description="Thematic tags (e.g., 'work_burnout', 'insomnia', 'relationship_conflict')",
    )
    suggested_habits: List[str] = Field(
        default_factory=list,
        description="Habit suggestions surfaced during the exchange",
    )
    crisis_signal_detected: bool = Field(
        default=False,
        description="True if the message contains crisis/self-harm indicators",
    )
    escalation_recommended: bool = Field(
        default=False,
        description="True if professional referral should be surfaced",
    )
    insight_summary: str = Field(
        default="",
        description="Brief narrative summary of the session insight",
    )
    risk_intensity_score: float = Field(
        default=1.0,
        ge=1.0,
        le=10.0,
        description="1–10 turn risk intensity from the dynamic risk assessor",
    )
    valence: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    arousal: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    confidence_score: float = Field(default=0.5, ge=0.0, le=1.0)


# ════════════════════════════════════════════════════════════════════════════
# Section 4 — Graph RAG Pipeline
# ════════════════════════════════════════════════════════════════════════════


class GraphNodeLabel(str, Enum):
    """
    Node types (labels) in the therapeutic knowledge graph (Neo4j).

    Each conversation turn can add or update nodes of any of these types.

    Values
    ------
    USER        : The person using the platform (singleton per user_id)
    ENTITY      : A person, place, or thing mentioned (e.g. "Mother", "Work")
    EVENT       : A specific life event (e.g. "Job Interview", "Breakup")
    EMOTION     : An emotional state (e.g. "Anxiety", "Loneliness")
    TRIGGER     : A situation that causes distress (e.g. "Work Deadlines")
    COPING_TOOL : A technique that helped (e.g. "8-Min Body Scan")
    SESSION     : A conversation session node (for temporal linking)
    """

    USER = "User"
    ENTITY = "Entity"
    EVENT = "Event"
    EMOTION = "Emotion"
    TRIGGER = "Trigger"
    COPING_TOOL = "CopingTool"
    SESSION = "Session"


class GraphRelationType(str, Enum):
    """
    Relationship types (edge labels) in the therapeutic knowledge graph.

    Values
    ------
    EXPERIENCES      : User EXPERIENCES Emotion (e.g. User → Anxiety)
    TRIGGERED_BY     : Emotion TRIGGERED_BY Trigger (e.g. Anxiety → Work Deadlines)
    ASSOCIATED_WITH  : General association between any two nodes
    TRIED_TOOL       : User TRIED_TOOL CopingTool (e.g. User → Body Scan)
    HELPED_WITH      : CopingTool HELPED_WITH Emotion or Trigger
    FOLLOWED_BY      : Temporal sequence between events or sessions
    PARTICIPATED_IN  : User PARTICIPATED_IN Session or Event
    """

    EXPERIENCES = "EXPERIENCES"
    TRIGGERED_BY = "TRIGGERED_BY"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    TRIED_TOOL = "TRIED_TOOL"
    HELPED_WITH = "HELPED_WITH"
    FOLLOWED_BY = "FOLLOWED_BY"
    PARTICIPATED_IN = "PARTICIPATED_IN"


class GraphTuple(BaseModel):
    """
    A single relational fact (subject-predicate-object triple) for Neo4j.

    Extracted by the LLM from a conversation turn and upserted into the
    knowledge graph via ``services/graph_rag.py::upsert_graph_tuples()``.

    Attributes
    ----------
    source_node : str
        Name/identifier of the source entity (e.g. ``"User"``,
        ``"Job Interview"``, ``"Mother"``).
    source_label : GraphNodeLabel
        Label/type of the source node.
    relationship : GraphRelationType
        The relationship connecting source to target.
    target_node : str
        Name/identifier of the target entity (e.g. ``"Anxiety"``,
        ``"Work"``, ``"Sleep Loss"``).
    target_label : GraphNodeLabel
        Label/type of the target node.
    properties : dict
        Optional metadata on the relationship (e.g. intensity score,
        session timestamp).  Merged onto the Neo4j relationship via
        ``SET r += $props``.
    """

    source_node: str = Field(
        ..., description="Name of the source entity (e.g., 'User', 'Job Interview', 'Mother')"
    )
    source_label: GraphNodeLabel = Field(
        ..., description="Label/type of the source node"
    )
    relationship: GraphRelationType = Field(
        ..., description="Relationship connecting source to target"
    )
    target_node: str = Field(
        ..., description="Name of the target entity (e.g., 'Anxiety', 'Work', 'Sleep Loss')"
    )
    target_label: GraphNodeLabel = Field(
        ..., description="Label/type of the target node"
    )
    properties: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata on the relationship (e.g., intensity, timestamp)",
    )


class ExtractedGraphData(BaseModel):
    """
    Container for all graph tuples extracted from a single conversation turn.

    Attributes
    ----------
    tuples : list[GraphTuple]
        List of relational facts ready to be upserted into Neo4j.
        May be empty if the LLM found no extractable facts.
    """

    tuples: List[GraphTuple] = Field(
        default_factory=list,
        description="List of relational facts to upsert into the knowledge graph",
    )


# ════════════════════════════════════════════════════════════════════════════
# Section 5 — Session Resumption & Safety
# ════════════════════════════════════════════════════════════════════════════


class CrisisResource(BaseModel):
    """
    Emergency contact resource surfaced when crisis signals are detected.

    Displayed in the crisis alert SSE event and in the UI sidebar.

    Attributes
    ----------
    name : str
        Human-readable name of the helpline (e.g. ``"Tele-MANAS"``).
    number : str
        Phone number string (may include country code prefix).
    description : str
        Brief description of the service and its availability.
    """

    name: str
    number: str
    description: str


class SessionResumeResponse(BaseModel):
    """
    Payload returned by GET /chat/session/{user_id}/resume.

    Designed to hydrate the chat UI state in under 500 ms on app launch
    so the user sees their conversation history immediately.

    Attributes
    ----------
    user_id : str
        The user whose session was resumed.
    session_id : str
        The resolved session ID (may differ from the request if the
        most recent session was auto-detected).
    is_resumed : bool
        Always ``True`` for a successful resumption response.
    last_message_timestamp : str, optional
        ISO-8601 timestamp of the most recent message in the session.
    dropped_session_context : str, optional
        A human-readable description of where the user left off
        (e.g. "User left mid-thought: 'I was feeling really...'").
    recent_messages : list[dict]
        The last 10 messages in the session, oldest first.
        Each dict has keys: ``role``, ``content``, ``timestamp``.
    active_emotional_state : str, optional
        The user's last recorded emotional state label, used to
        pre-populate the UI's mood indicator.
    """

    user_id: str
    session_id: str
    is_resumed: bool = True
    last_message_timestamp: Optional[str] = None
    dropped_session_context: Optional[str] = None
    recent_messages: List[Dict[str, Any]] = Field(default_factory=list)
    active_emotional_state: Optional[str] = None


class StreamChunk(BaseModel):
    """
    A single SSE event emitted by the /chat/stream endpoint.

    The ``event`` field maps to the SSE ``event:`` header line; the
    ``data`` field maps to the ``data:`` line(s).

    Attributes
    ----------
    event : str
        One of:
        - ``"token"``       — a partial text token as the LLM generates it
        - ``"action_card"`` — a complete action card JSON payload
        - ``"crisis_alert"``— emergency crisis intervention resources
        - ``"error"``       — streaming error payload
        - ``"done"``        — signals the stream has completed
    data : Any
        The payload associated with the event.  For ``"token"`` events
        this is a dict ``{"token": "..."}``; for card/crisis events it
        is the full JSON object.
    """

    event: str  # "token" | "action_card" | "crisis_alert" | "error" | "done"
    data: Any


# ════════════════════════════════════════════════════════════════════════════
# Section 6 — Longitudinal User Pattern Detection
# ════════════════════════════════════════════════════════════════════════════


class PatternType(str, Enum):
    TEMPORAL = "TEMPORAL"
    BEHAVIORAL = "BEHAVIORAL"
    CROSS_DOMAIN = "CROSS_DOMAIN"
    ACADEMIC = "ACADEMIC"
    INTERVENTION_RESPONSE = "INTERVENTION_RESPONSE"
    RECURRENCE = "RECURRENCE"
    CHANGE_POINT = "CHANGE_POINT"


class PatternStatus(str, Enum):
    OBSERVATION = "OBSERVATION"
    EMERGING = "EMERGING"
    ESTABLISHED = "ESTABLISHED"
    ESTABLISHED_PERSISTENT_DISTRESS = "ESTABLISHED_PERSISTENT_DISTRESS"
    INACTIVE = "INACTIVE"


class PatternDomain(str, Enum):
    SLEEP = "sleep"
    JOURNALING = "journaling"
    TASKS = "tasks"
    MEDITATION = "meditation"
    LANGUAGE = "language"
    ACADEMIC = "academic"
    ATTENDANCE = "attendance"
    CONVERSATION = "conversation"
    MOOD = "mood"
    HABITS = "habits"
    GRAPH = "graph"
    APM = "apm"


class PatternFeedbackEvent(str, Enum):
    CONFIRM = "CONFIRM"
    DISAGREE = "DISAGREE"
    NOT_RELATED = "NOT_RELATED"
    HELPFUL = "HELPFUL"
    NOT_HELPFUL = "NOT_HELPFUL"
    DISMISS = "DISMISS"
    STARTED = "STARTED"


class PatternFeedbackRequest(BaseModel):
    pattern_id: str
    event_type: PatternFeedbackEvent
    note: Optional[str] = None

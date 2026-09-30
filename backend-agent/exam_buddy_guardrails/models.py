"""Guardrail and learning-graph types for Exam Buddy."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RequestCategory(str, Enum):
    ACADEMIC = "ACADEMIC"
    NON_ACADEMIC = "NON_ACADEMIC"
    UNSAFE = "UNSAFE"
    UNCLEAR = "UNCLEAR"


class NodeType(str, Enum):
    USER = "USER"
    SUBJECT = "SUBJECT"
    TOPIC = "TOPIC"
    CONCEPT = "CONCEPT"
    MISTAKE = "MISTAKE"
    LEARNING_PREFERENCE = "LEARNING_PREFERENCE"
    EXAM = "EXAM"
    STUDY_GOAL = "STUDY_GOAL"
    CONVERSATION = "CONVERSATION"


class RelationType(str, Enum):
    STUDIES = "STUDIES"
    LEARNING = "LEARNING"
    MASTERED = "MASTERED"
    STRUGGLES_WITH = "STRUGGLES_WITH"
    MADE_MISTAKE_IN = "MADE_MISTAKE_IN"
    PREFERS = "PREFERS"
    PREPARING_FOR = "PREPARING_FOR"
    HAS_GOAL = "HAS_GOAL"
    DISCUSSED = "DISCUSSED"
    RELATED_TO = "RELATED_TO"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class MemoryCandidate(BaseModel):
    relation: RelationType
    node_type: NodeType
    label: str


class RetrievedMemory(BaseModel):
    relation: str
    node_type: str
    label: str
    score: int = 1


class ExamBuddyTurn(BaseModel):
    category: RequestCategory
    reply: str
    routed_to: str
    memory_used: bool = False
    memories: list[str] = Field(default_factory=list)
    user_id: Optional[str] = None

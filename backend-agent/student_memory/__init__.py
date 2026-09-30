"""Durable facts about a student, with importance that fades unless confirmed.

Facts come from session reports. Chat turns do not write here.
Writes require personalization consent. Crisis sessions write nothing.
"""

from student_memory.context import build_memory_context
from student_memory.store import consolidate_student_memory, retrieve_facts, upsert_facts

__all__ = [
    "build_memory_context",
    "consolidate_student_memory",
    "retrieve_facts",
    "upsert_facts",
]

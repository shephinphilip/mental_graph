"""Exam Buddy guardrails and learning-graph memory.

Academic answers come from the existing LLM. This package only decides
whether a turn is academic, retrieves that student's learning memory, and
checks the reply. It does not store textbook knowledge.
"""

from exam_buddy_guardrails.services.exam_buddy_service import handle_exam_buddy_turn

__all__ = ["handle_exam_buddy_turn"]

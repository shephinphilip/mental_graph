"""First gate. Decides whether Exam Buddy may answer at all."""

from __future__ import annotations

from exam_buddy_guardrails.guardrails.classifier import classify_request
from exam_buddy_guardrails.models import RequestCategory


def screen_input(text: str) -> RequestCategory:
    return classify_request(text)

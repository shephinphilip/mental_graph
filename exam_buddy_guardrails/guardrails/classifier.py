"""Intent classification for Exam Buddy. A topic word alone is not enough."""

from __future__ import annotations

import re

from services.safety_class import SafetyClass, classify_message

from exam_buddy_guardrails.models import RequestCategory

_ACADEMIC_TASK = re.compile(
    r"\b("
    r"explain|solve|calculate|prove|derive|define|simplify|homework|"
    r"what is|what are|how does|how do i|show me how|formula|theorem|"
    r"textbook|syllabus|chapter|concept"
    r")\b",
    re.IGNORECASE,
)
_WELLNESS = re.compile(
    r"\b("
    r"i feel|i'm feeling|i am feeling|i am experiencing|i'm experiencing|"
    r"feel lonely|feeling lonely|severe anxiety|what should i do|"
    r"can't sleep|cannot sleep|help me calm|my relationship|meditat|"
    r"journal|i am so sad|i'm so sad|i am anxious|i'm anxious"
    r")\b",
    re.IGNORECASE,
)
_SUBJECT = re.compile(
    r"\b("
    r"maths?|mathematics|algebra|equation|calculus|geometry|"
    r"physics|chemistry|biology|thermodynamics|newton|"
    r"computer|algorithm|binary search|statistics|probability|"
    r"reasoning|homework|exam"
    r")\b",
    re.IGNORECASE,
)


def classify_request(text: str) -> RequestCategory:
    """Return the intent of one student message.

    Crisis language is unsafe even when a school subject is also mentioned.
    A textbook-style question about anxiety stays academic. A first-person
    request for help with anxiety does not.
    """
    raw = (text or "").strip()
    if len(raw) < 3:
        return RequestCategory.UNCLEAR
    if classify_message(raw) is not SafetyClass.NONE:
        return RequestCategory.UNSAFE

    academic = bool(_ACADEMIC_TASK.search(raw)) or (
        bool(_SUBJECT.search(raw)) and "?" in raw
    )
    wellness = bool(_WELLNESS.search(raw))
    if wellness and not academic:
        return RequestCategory.NON_ACADEMIC
    if academic:
        return RequestCategory.ACADEMIC
    return RequestCategory.UNCLEAR

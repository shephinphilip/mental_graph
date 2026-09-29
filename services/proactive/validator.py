"""Candidate gate plus final user-facing response guarantee.

Reuses ``services.response_validator.validate_reply`` for script, diagnosis
phrases, and the platform one-question ``?`` ceiling. Adds proactive-specific
checks that do not rely on question-mark counting alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.apm import contains_crisis_signal
from services.proactive.question_generator import soften_once
from services.proactive.schemas import QuestionCandidate
from services.response_validator import Validation, validate_reply
from services.safety_class import SafetyClass, classify_message

_DIAGNOSIS = (
    "you have depression",
    "you have anxiety",
    "you have adhd",
    "you are depressed",
    "you're depressed",
    "you are anxious",
    "you're anxious",
    "you seem anxious",
    "you seem depressed",
    "this is burnout",
    "you are burnt out",
    "you're struggling",
    "you are struggling",
    "are you depressed",
    "are you anxious",
    "worried about failing",
)
_INTERNALS = (
    "graph shows",
    "your graph",
    "node_id",
    "latent state",
    "confidence score",
    "risk score",
    "risk_intensity",
    "apm",
    "telemetry",
    "typing speed",
    "opened the app",
    "keystroke",
    "recent activity suggests",
    "your recent activity",
)
_PRESSURE = (
    "you should",
    "you need to",
    "you must",
    "why did you stop",
    "make sure you",
)
_UNNECESSARY_PII = (
    "password",
    "address",
    "phone number",
    "where do you live",
    "send a photo",
)
_QUESTION_MARKS = ("?", "？", "؟")
_INTERROGATIVE = re.compile(
    r"\b(?:how|what|why|when|where|who|which)\b"
    r"|\b(?:are|do|did|does|is|was|has|have|had|can|could|would|will)\s+you\b"
    r"|\bhas\s+it\b",
    re.IGNORECASE,
)
_STACKED_AND = re.compile(
    r"\b(?:and(?:\s+also)?)\s+"
    r"(?:what|how|why|when|where|who|which|are\s+you|do\s+you|did\s+you|has\s+it)\b",
    re.IGNORECASE,
)
_DOUBLE_IMPERATIVE = re.compile(
    r"\btell me\b.+\b(?:and(?:\s+also)?)\s+(?:explain|tell|why)\b"
    r"|\bexplain\b.+\band(?:\s+also)?\s+(?:why|what|how)\b",
    re.IGNORECASE | re.DOTALL,
)
_SCORE_LEAK = re.compile(
    r"\b(?:confidence|risk(?:\s+score)?|intensity)\b\s*[:=]?\s*\d",
    re.IGNORECASE,
)
_STOP = frozenset({"that", "this", "with", "from", "your", "have", "been", "still"})


def question_mark_count(text: str) -> int:
    body = text or ""
    return sum(body.count(mark) for mark in _QUESTION_MARKS)


def _topic_tokens(topic: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-Z]{4,}", (topic or "").casefold())
        if token not in _STOP
    }


def _meaningful_question_count(text: str) -> int:
    body = text or ""
    marks = question_mark_count(body)
    if marks > 1:
        return marks
    if _DOUBLE_IMPERATIVE.search(body):
        return 2
    if _STACKED_AND.search(body):
        return 2
    hits = list(_INTERROGATIVE.finditer(body))
    if len(hits) >= 2:
        between = body[hits[0].end() : hits[1].start()].casefold()
        if re.search(r"\bor\b", between) and not re.search(r"\band\b", between):
            return max(1, marks)
        if re.search(r"\band\b", between) or "," in between:
            return 2
    return max(marks, 1 if hits or marks else 0)


def _shared_content_failures(text: str) -> str:
    lowered = (text or "").casefold()
    if any(term in lowered for term in _DIAGNOSIS):
        return "diagnosis"
    if any(term in lowered for term in _INTERNALS) or _SCORE_LEAK.search(text or ""):
        return "exposes_internals"
    if any(term in lowered for term in _PRESSURE) or "why did you" in lowered:
        return "pressures_user" if "why did you" not in lowered else "interrogation"
    if any(term in lowered for term in _UNNECESSARY_PII):
        return "unnecessary_personal_information"
    if contains_crisis_signal(text or ""):
        return "harmful_guidance"
    label = classify_message(text or "")
    if label is not SafetyClass.NONE:
        return "harmful_guidance"
    return ""


def validate_candidate(
    candidate: QuestionCandidate,
    *,
    retry: bool = False,
) -> tuple[QuestionCandidate | None, str]:
    question = (candidate.question or "").strip()
    if not question:
        return None, "empty_question"
    if _meaningful_question_count(question) > 1:
        if retry:
            return None, "excessive_questions"
        candidate.question = soften_once(question)
        return validate_candidate(candidate, retry=True)
    if _meaningful_question_count(question) < 1:
        return None, "not_a_question"
    shared = _shared_content_failures(question)
    if shared:
        return None, shared
    verdict = validate_reply(
        question,
        script=candidate.script or "LATIN",
        pattern_supplied=False,
    )
    if not verdict.ok:
        if retry or verdict.reason != "excessive_questions":
            return None, verdict.reason or "validator_reject"
        candidate.question = soften_once(question)
        return validate_candidate(candidate, retry=True)
    if (candidate.script or "").upper() == "ROMAN":
        from services.response_validator import _native_script

        if _native_script(question):
            return None, "roman_script_violation"
    return candidate, ""


@dataclass(frozen=True)
class FinalProactiveValidation:
    ok: bool
    reason: str = ""


def validate_final_response(
    text: str,
    *,
    topic: str = "",
    question: str = "",
    script: str = "LATIN",
    language: str = "ENGLISH",
    pattern_supplied: bool = False,
) -> FinalProactiveValidation:
    """Validate the user-visible assistant reply, not the pre-LLM candidate."""
    body = (text or "").strip()
    if not body:
        return FinalProactiveValidation(False, "empty_reply")
    count = _meaningful_question_count(body)
    if count > 1:
        return FinalProactiveValidation(False, "excessive_questions")
    if count < 1:
        return FinalProactiveValidation(False, "ignored_proactive_instruction")
    shared = _shared_content_failures(body)
    if shared:
        return FinalProactiveValidation(False, shared)
    verdict = validate_reply(body, script=script or "LATIN", pattern_supplied=pattern_supplied)
    if not verdict.ok:
        return FinalProactiveValidation(False, verdict.reason or "validator_reject")
    lang = (language or "ENGLISH").upper()
    script_name = (script or "LATIN").upper()
    if script_name == "ROMAN":
        from services.response_validator import _native_script

        if _native_script(body):
            return FinalProactiveValidation(False, "roman_script_violation")
    tokens = _topic_tokens(topic) or _topic_tokens(question)
    if tokens and lang in {"ENGLISH", "HINGLISH"} and script_name in {"LATIN", "ROMAN"}:
        lowered = body.casefold()
        if not any(token in lowered for token in tokens):
            return FinalProactiveValidation(False, "intent_mismatch")
    return FinalProactiveValidation(True)

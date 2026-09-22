"""Tests for read-only marks context and academic turn classification."""

from services.marks import build_marks_context, classification_hint
from prompts import format_system_prompt, WELCOME_USER_CUE


def test_greeting_skips_marks_class():
    assert classification_hint("hi") == "SAFE"
    assert classification_hint("Hello!") == "SAFE"
    assert classification_hint(WELCOME_USER_CUE) == "SAFE"


def test_exam_language_is_academic():
    assert classification_hint("My JEE mock was a disaster") == "EXAM_STRESS"
    assert classification_hint("Can we talk about my Physics marks?") == "MARKS"


def test_build_marks_context_trend_and_empty():
    assert build_marks_context([]) == "No academic data available"
    block = build_marks_context(
        [
            {"subject": "Physics", "exam_type": "JEE Mock", "marks": 42, "total_marks": 100, "percentage": 42, "rank": 10, "exam_date": None},
            {"subject": "Physics", "exam_type": "JEE Mock", "marks": 31, "total_marks": 100, "percentage": 31, "rank": 20, "exam_date": None},
            {"subject": "Physics", "exam_type": "JEE Mock", "marks": 28, "total_marks": 100, "percentage": 28, "rank": 30, "exam_date": None},
        ]
    )
    assert "Declining" in block
    assert "Physics" in block


def test_prompt_includes_last_session_placeholder():
    text = format_system_prompt(last_session_context="Previous session talked about sleep.")
    assert "Previous session talked about sleep." in text
    assert "[Session open]" in WELCOME_USER_CUE

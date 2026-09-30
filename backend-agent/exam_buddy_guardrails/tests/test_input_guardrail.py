"""Academic, wellness, and crisis intents stay distinct."""

from exam_buddy_guardrails.guardrails.input_guardrail import screen_input
from exam_buddy_guardrails.models import RequestCategory


def test_newton_question_is_academic():
    assert screen_input("What is Newton's second law?") is RequestCategory.ACADEMIC


def test_textbook_anxiety_is_academic_and_personal_anxiety_is_not():
    assert (
        screen_input("Explain anxiety from a psychology textbook.")
        is RequestCategory.ACADEMIC
    )
    assert (
        screen_input("I am experiencing severe anxiety. What should I do?")
        is RequestCategory.NON_ACADEMIC
    )


def test_loneliness_is_not_sent_to_exam_buddy():
    assert screen_input("I feel lonely.") is RequestCategory.NON_ACADEMIC


def test_crisis_is_unsafe_even_if_a_subject_is_mentioned():
    assert (
        screen_input("I want to die and I cannot do algebra.")
        is RequestCategory.UNSAFE
    )


def test_blank_input_is_unclear():
    assert screen_input("hi") is RequestCategory.UNCLEAR

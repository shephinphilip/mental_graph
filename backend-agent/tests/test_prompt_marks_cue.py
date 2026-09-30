"""Prompt text that used to be asserted beside the marks classifier."""

from backend_core.marks import classification_hint
from prompts import WELCOME_USER_CUE, format_system_prompt


def test_welcome_cue_is_not_classified_as_marks():
    assert classification_hint(WELCOME_USER_CUE) == "SAFE"
    assert "[Session open]" in WELCOME_USER_CUE


def test_prompt_includes_last_session_placeholder():
    text = format_system_prompt(last_session_context="Previous session talked about sleep.")
    assert "Previous session talked about sleep." in text

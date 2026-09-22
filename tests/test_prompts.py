"""Smoke tests for Zenark system-prompt assembly."""

from prompts import (
    SESSION_PHASE_CONTINUING,
    SESSION_PHASE_OPENING,
    dropped_session_hint,
    format_system_prompt,
    session_phase_instructions,
)
from services.inner_council import deliberate


def test_format_system_prompt_fills_defaults():
    text = format_system_prompt()
    assert "You are Zenark" in text
    assert "No academic data available" in text
    assert "No attendance data available" in text
    assert "LISTEN FIRST" in text
    assert "REFLECTIVE CONTAINMENT" in text
    assert "CONTEXTUAL UNCERTAINTY" in text
    assert "WARM BUT NOT HOLLOW" in text
    assert "Never re-greet" in text
    assert "You are never the crisis" in text.lower() or "NEVER THE CRISIS" in text


def test_format_system_prompt_injects_memory_and_graph():
    text = format_system_prompt(
        graph_context="User —experienced→ breakup",
        user_memory="Has mentioned loneliness at night.",
        adaptive_memory_context=(
            "State: fatigue -> grounding (background_only, inferred=True)"
        ),
    )
    assert "User —experienced→ breakup" in text
    assert "Has mentioned loneliness at night." in text
    assert "background_only" in text
    assert "inferred must never create" in text


def test_session_phase_opening_turn_only():
    opening = session_phase_instructions(opening_turn=True)
    assert opening == SESSION_PHASE_OPENING
    assert "SESSION OPENING ONLY" in opening

    mid = session_phase_instructions(
        opening_turn=False,
        message_history=[
            {"role": "assistant", "content": "Hello Meera."},
            {"role": "user", "content": "I have something to say."},
        ],
    )
    assert mid == SESSION_PHASE_CONTINUING
    assert "Never re-greet" in mid


def test_format_system_prompt_mid_session_omits_opening_speak_first():
    text = format_system_prompt(
        session_phase=session_phase_instructions(
            opening_turn=False,
            message_history=[{"role": "assistant", "content": "hi"}],
        ),
    )
    assert "MID-SESSION" in text
    assert "Never re-greet" in text
    assert "chronologically" in text.lower() or "chronological" in text.lower()
    # Opening-only speak-first cue must not appear in phase block
    assert "SESSION OPENING ONLY" not in text


def test_format_system_prompt_opening_includes_speak_first():
    text = format_system_prompt(
        session_phase=session_phase_instructions(opening_turn=True),
    )
    assert "SESSION OPENING ONLY" in text
    assert "Speak first" in text


def test_format_system_prompt_injects_inner_council_stance():
    stance = deliberate(
        "I have a crush on someone. But I didn't tell him. He has a girlfriend.",
        [{"role": "assistant", "content": "What's on your mind?"}],
    )
    text = format_system_prompt(response_stance=stance.as_prompt_block())
    assert "INNER COUNCIL" in text
    assert "Reflective Containment" in text
    assert "Empathy Agent" in text


def test_dropped_session_hint_unanswered_user_turn():
    hint = dropped_session_hint(
        [{"role": "user", "content": "I feel off lately"}]
    )
    assert "mid-thought" in hint
    assert "I feel off lately" in hint


def test_dropped_session_hint_mid_session_continuity():
    hint = dropped_session_hint(
        [
            {"role": "assistant", "content": "What's on your mind?"},
            {"role": "user", "content": "Something happened today."},
        ]
    )
    assert "Mid-session continuity" in hint
    assert "Do not greet again" in hint

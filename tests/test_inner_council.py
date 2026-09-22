"""Tests for Inner Council deliberation briefs."""

from services.inner_council import deliberate


def test_crush_disclosure_gets_reflective_containment_brief():
    stance = deliberate(
        "I have a crush on someone. But I didn't tell him. He has a girlfriend.",
        [
            {"role": "assistant", "content": "What's been going on?"},
            {"role": "user", "content": "I have something to say about today's incident."},
        ],
    )
    assert stance.risk_band == "none"
    assert "Reflective Containment" in stance.consensus_brief
    assert "Never ask why it matters" in stance.consensus_brief
    assert "vulnerability" in stance.empathy_focus.lower() or "secrecy" in stance.empathy_focus.lower()
    assert stance.confidence in {"moderate", "high"}


def test_short_ambiguous_turn_is_tentative_and_brief():
    stance = deliberate("Something happened.")
    assert stance.confidence == "low"
    assert stance.verbosity == "brief"
    assert "tentative" in stance.consensus_brief.lower() or "It sounds like" in stance.consensus_brief


def test_overload_markers_force_brief_containment():
    stance = deliberate("I'm so anxious and exhausted I can't sleep")
    assert stance.verbosity == "brief"
    assert stance.risk_band == "elevated"
    assert "containment" in stance.empathy_focus.lower() or "Contain" in stance.consensus_brief


def test_crisis_adjacent_elevates_risk_without_interrogation():
    stance = deliberate("I want to die")
    assert stance.risk_band == "crisis_adjacent"
    assert "Do not interrogate" in stance.consensus_brief


def test_opening_turn_brief_is_welcome_scoped():
    stance = deliberate("", opening_turn=True)
    assert "welcome" in stance.consensus_brief.lower()
    assert "No multi-question" in stance.consensus_brief

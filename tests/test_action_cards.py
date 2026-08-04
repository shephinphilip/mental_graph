"""
Unit tests for the action card parser.

Covers:
    • Single card extraction
    • Multiple cards in one message
    • No cards present
    • Malformed JSON (graceful skip)
    • Partial / broken delimiters
    • Reply text is clean after extraction
"""

import pytest

from schemas import ActionCard, CardType
from services.action_cards import parse_action_cards


# ── Single Card ─────────────────────────────────────────────────────────────


def test_single_tool_card():
    raw = (
        "I think a body scan might help you unwind tonight.\n\n"
        '<<<ACTION_CARD\n'
        '{\n'
        '  "card_type": "TOOL_CARD",\n'
        '  "title": "8-Minute Body Scan",\n'
        '  "subtitle": "A guided relaxation exercise",\n'
        '  "action_payload": {"resource_id": "body_scan_8min"}\n'
        '}\n'
        'ACTION_CARD>>>'
    )
    reply, cards = parse_action_cards(raw)

    assert reply == "I think a body scan might help you unwind tonight."
    assert len(cards) == 1
    assert cards[0].card_type == CardType.TOOL
    assert cards[0].title == "8-Minute Body Scan"
    assert cards[0].action_payload["resource_id"] == "body_scan_8min"


def test_habit_card():
    raw = (
        "That sounds like a great plan!\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "HABIT_CARD", "title": "Morning Water", '
        '"subtitle": "Drink 500ml after waking", '
        '"action_payload": {"habit_title": "Drink water", "frequency": "daily"}}\n'
        'ACTION_CARD>>>'
    )
    reply, cards = parse_action_cards(raw)

    assert reply == "That sounds like a great plan!"
    assert len(cards) == 1
    assert cards[0].card_type == CardType.HABIT


def test_booking_card():
    raw = (
        "It might be really valuable to talk to someone trained in this.\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "BOOKING_CARD", "title": "Connect with a Therapist", '
        '"action_payload": {"flow": "therapy_booking"}}\n'
        'ACTION_CARD>>>'
    )
    _, cards = parse_action_cards(raw)
    assert len(cards) == 1
    assert cards[0].card_type == CardType.BOOKING


# ── Multiple Cards ──────────────────────────────────────────────────────────


def test_multiple_cards():
    raw = (
        "Here are a couple of things that might help.\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "TOOL_CARD", "title": "Breathing Exercise", '
        '"action_payload": {"resource_id": "breathe_4_7_8"}}\n'
        'ACTION_CARD>>>\n\n'
        '<<<ACTION_CARD\n'
        '{"card_type": "CONTENT_CARD", "title": "Understanding Anxiety", '
        '"subtitle": "10-min read", '
        '"action_payload": {"module_id": "anxiety_101"}}\n'
        'ACTION_CARD>>>'
    )
    reply, cards = parse_action_cards(raw)

    assert reply == "Here are a couple of things that might help."
    assert len(cards) == 2
    assert cards[0].card_type == CardType.TOOL
    assert cards[1].card_type == CardType.CONTENT


# ── No Cards ────────────────────────────────────────────────────────────────


def test_no_cards():
    raw = "I hear you. That sounds really difficult, and it's okay to feel that way."
    reply, cards = parse_action_cards(raw)

    assert reply == raw
    assert cards == []


def test_empty_string():
    reply, cards = parse_action_cards("")
    assert reply == ""
    assert cards == []


# ── Malformed / Edge Cases ──────────────────────────────────────────────────


def test_malformed_json_skipped():
    """Malformed JSON inside delimiters should be silently skipped."""
    raw = (
        "Let me suggest something.\n\n"
        '<<<ACTION_CARD\n'
        '{bad json here}\n'
        'ACTION_CARD>>>'
    )
    reply, cards = parse_action_cards(raw)

    assert reply == "Let me suggest something."
    assert cards == []


def test_partial_delimiter_not_matched():
    """Text with only the opening delimiter should not be treated as a card."""
    raw = "Some text <<<ACTION_CARD {\"card_type\": \"TOOL_CARD\"} but no closing."
    reply, cards = parse_action_cards(raw)

    assert cards == []
    assert reply == raw


def test_valid_and_invalid_mixed():
    """One valid card and one malformed — only the valid one should parse."""
    raw = (
        "Here you go.\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "TASK_CARD", "title": "Journal Entry", '
        '"action_payload": {"task": "write_journal"}}\n'
        'ACTION_CARD>>>\n\n'
        '<<<ACTION_CARD\n'
        '{not valid}\n'
        'ACTION_CARD>>>'
    )
    reply, cards = parse_action_cards(raw)

    assert reply == "Here you go."
    assert len(cards) == 1
    assert cards[0].card_type == CardType.TASK


def test_missing_required_field_skipped():
    """Card JSON missing 'action_payload' (required) should be skipped."""
    raw = (
        "Try this.\n\n"
        '<<<ACTION_CARD\n'
        '{"card_type": "TOOL_CARD", "title": "Breathing"}\n'
        'ACTION_CARD>>>'
    )
    reply, cards = parse_action_cards(raw)

    assert reply == "Try this."
    assert cards == []


# ── Whitespace Handling ─────────────────────────────────────────────────────


def test_extra_whitespace_around_card():
    raw = (
        "  Some reply text  \n\n\n"
        '  <<<ACTION_CARD  \n'
        '  {"card_type": "HABIT_CARD", "title": "Stretch", '
        '"action_payload": {"habit": "morning_stretch"}}  \n'
        '  ACTION_CARD>>>  \n\n'
    )
    reply, cards = parse_action_cards(raw)

    assert "Some reply text" in reply
    assert len(cards) == 1

"""Tests for 1–10 risk scoring, sliding window, and psychiatrist cards."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from prompts import format_system_prompt
from schemas import PatternFeedbackEvent, PatternStatus, SessionExtraction
from services.action_cards import (
    PSYCHIATRIST_CARD_ID,
    build_psychiatrist_referral_card,
    ensure_psychiatrist_card,
)
from services.inner_council import deliberate
from services.patterns.window import format_action_card_context, record_and_evaluate
from services.risk_assessor import score_turn
from tests.test_patterns import FakeCollection, _db


def test_ordinary_turn_stays_in_typical_band():
    score = score_turn("I have a crush on someone. He has a girlfriend.")
    assert 1.0 <= score.risk_intensity_score <= 7.0
    assert score.crisis_keywords is False


def test_severe_distress_reaches_high_band_without_crisis_keyword():
    score = score_turn(
        "I feel hopeless and worthless. I can't take it. Everything is unbearable."
    )
    assert score.risk_intensity_score >= 8.0
    assert score.crisis_keywords is False
    assert score.band == "high"


def test_crisis_keywords_score_in_8_to_10():
    score = score_turn("I want to die")
    assert score.risk_intensity_score >= 8.0
    assert score.crisis_keywords is True


def test_session_extraction_accepts_risk_fields():
    extraction = SessionExtraction(
        detected_emotions=["sad"],
        risk_intensity_score=6.5,
        valence=-0.4,
        arousal=0.3,
        confidence_score=0.7,
    )
    assert extraction.risk_intensity_score == 6.5
    payload = extraction.model_dump()
    assert "valence" in payload
    assert "arousal" in payload


@pytest.mark.asyncio
async def test_single_high_score_does_not_attach_card():
    db = _db()
    db._c["user_risk_turns"] = FakeCollection()
    decision = await record_and_evaluate(
        db,
        user_id="u1",
        session_id="s1",
        message="I feel hopeless and worthless. I can't take it. Unbearable.",
    )
    assert decision.score.risk_intensity_score >= 8.0
    assert decision.attach_psychiatrist_card is False
    assert decision.persistent_distress is False


@pytest.mark.asyncio
async def test_three_consecutive_high_scores_attach_psychiatrist_card():
    db = _db()
    db._c["user_risk_turns"] = FakeCollection()
    heavy = "I feel hopeless and worthless. I can't take it. Everything is unbearable."
    last = None
    for i in range(3):
        last = await record_and_evaluate(
            db, user_id="u1", session_id="s1", message=heavy
        )
    assert last is not None
    assert last.consecutive_high >= 3
    assert last.persistent_distress is True
    assert last.attach_psychiatrist_card is True
    assert last.action_card_context["suggesting_card"] == "PSYCHIATRIST_REFERRAL"
    stored = db._c["user_patterns"].docs
    assert any(
        d.get("status") == PatternStatus.ESTABLISHED_PERSISTENT_DISTRESS.value
        for d in stored
    )


@pytest.mark.asyncio
async def test_crisis_turns_do_not_count_toward_persistent_window():
    db = _db()
    db._c["user_risk_turns"] = FakeCollection()
    last = None
    for _ in range(3):
        last = await record_and_evaluate(
            db, user_id="u1", session_id="s1", message="I want to die"
        )
    assert last.score.crisis_keywords is True
    assert last.attach_psychiatrist_card is False
    assert last.consecutive_high == 0


@pytest.mark.asyncio
async def test_dismiss_puts_card_on_cooldown():
    db = _db()
    db._c["user_risk_turns"] = FakeCollection()
    heavy = "I feel hopeless and worthless. I can't take it. Everything is unbearable."
    last = None
    for _ in range(3):
        last = await record_and_evaluate(
            db, user_id="u1", session_id="s1", message=heavy
        )
    from services.patterns.feedback import record_pattern_feedback

    await record_pattern_feedback(
        db,
        user_id="u1",
        pattern_id=last.pattern_id,
        event_type=PatternFeedbackEvent.DISMISS,
    )
    again = await record_and_evaluate(
        db, user_id="u1", session_id="s1", message=heavy
    )
    assert again.persistent_distress is True
    assert again.attach_psychiatrist_card is False


def test_psychiatrist_card_payload_shape():
    card = build_psychiatrist_referral_card(pattern_id="pat_1", trigger_reason="window")
    dumped = card.model_dump()
    assert dumped["card_id"] == PSYCHIATRIST_CARD_ID
    assert dumped["cta_label"] == "Explore Care Options"
    assert dumped["action_payload"]["type"] == "PSYCHIATRIST_REFERRAL"
    assert dumped["action_payload"]["execution_nonce"]
    merged = ensure_psychiatrist_card([], attach=True, pattern_id="pat_1")
    assert len(merged) == 1
    assert ensure_psychiatrist_card(merged, attach=True) == merged


def test_action_card_context_injected_into_system_prompt():
    block = format_action_card_context(
        {
            "suggesting_card": "PSYCHIATRIST_REFERRAL",
            "trigger_reason": "Sustained high distress",
            "tone_instruction": "Warm, non-pushy.",
        }
    )
    text = format_system_prompt(action_card_context=block)
    assert "PSYCHIATRIST_REFERRAL" in text
    assert "Do NOT emit another ACTION_CARD" in text
    stance = deliberate(
        "I feel hopeless and worthless. I can't take it. Unbearable.",
        attach_psychiatrist_card=True,
        risk_intensity_score=8.6,
        persistent_distress=True,
    )
    assert stance.attach_psychiatrist_card is True
    assert "psychiatrist" in stance.consensus_brief.lower()

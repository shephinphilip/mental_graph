"""Hardening pass: final-response guarantee, delivery linkage, retrieval, language."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from mongomock_motor import AsyncMongoMockClient

from services.apm import ensure_apm_indexes
from services.erasure import start_erasure
from services.proactive.delivery import (
    commit_delivery,
    ensure_final_proactive_reply,
    suppress_undelivered,
)
from services.proactive.observability import reset_for_tests
from services.proactive.schemas import Decision, ProactiveStatus, QuestionCandidate
from services.proactive.service import (
    evaluate_proactive_question,
    mark_delivered,
    record_proactive_response,
)
from services.proactive.store import (
    COLLECTION,
    ensure_proactive_indexes,
    get_event,
    insert_opportunity,
    mark_status,
)
from services.proactive.trigger_engine import retrieve_bounded_context
from services.proactive.validator import validate_candidate, validate_final_response
from tests.test_proactive import _deliver_event, _seed_trigger
from tests.test_streaming import _make_mock_db


VALID = (
    "I remember that presentation was weighing on you a bit. "
    "How did it end up feeling once it was over?"
)
QUESTION = (
    "I wonder if the presentation is still sitting in the back of your mind. "
    "Has it eased up at all?"
)
TOPIC = "the presentation"

# Opening / empty-message evaluate uses the existing resolver: Indian languages → ROMAN.
ROMAN_EVALUATE_SAMPLES = {
    "ENGLISH": VALID,
    "HINGLISH": "Woh presentation ab bhi mann mein hai kya?",
    "HINDI": "Kya woh presentation ab bhi mann mein hai?",
    "MALAYALAM": "Aa presentation ippozhum manasil undo?",
    "TAMIL": "Andha presentation innum manasula irukka?",
    "TELUGU": "Aa presentation inka manasulo unda?",
    "KANNADA": "Aa presentation innu manassinalideya?",
}

NATIVE_VALIDATOR_SAMPLES = {
    ("HINDI", "DEVANAGARI"): "क्या वो प्रस्तुति अभी भी दिमाग में है?",
    ("MALAYALAM", "MALAYALAM"): "ആ പ്രസന്റേഷൻ ഇപ്പോഴും മനസ്സിലുണ്ടോ?",
    ("TAMIL", "TAMIL"): "அந்த presentation இன்னும் மனதில் இருக்கா?",
    ("TELUGU", "TELUGU"): "ఆ presentation ఇంకా మనసులో ఉందా?",
    ("KANNADA", "KANNADA"): "ಆ presentation ಇನ್ನೂ ಮನಸ್ಸಿನಲ್ಲಿದೆಯೇ?",
}


@pytest_asyncio.fixture
async def db():
    reset_for_tests()
    database = AsyncMongoMockClient()["proactive_hardening"]
    await ensure_apm_indexes(database)
    await ensure_proactive_indexes(database)
    users = [
        {
            "user_id": "user_A",
            "isActive": True,
            "personalization_consent": True,
            "preferred_language": "ENGLISH",
            "age": 16,
        },
        {
            "user_id": "no_consent",
            "isActive": True,
            "personalization_consent": False,
            "preferred_language": "ENGLISH",
            "age": 16,
        },
        {
            "user_id": "lang_HINDI_NATIVE",
            "isActive": True,
            "personalization_consent": True,
            "preferred_language": "HINDI",
            "age": 16,
        },
    ]
    for language in ROMAN_EVALUATE_SAMPLES:
        users.append(
            {
                "user_id": f"lang_{language}",
                "isActive": True,
                "personalization_consent": True,
                "preferred_language": language,
                "age": 16,
            }
        )
    await database["users"].insert_many(users)
    return database


async def _approved(db, user_id: str = "user_A", status: str = "APPROVED"):
    stored = await insert_opportunity(
        db,
        user_id=user_id,
        event_id="pq_hard_1",
        execution_nonce="hardeningnonce123456789012",
        trigger_type="FOLLOW_UP_ON_PREVIOUS_CONTEXT",
        question=QUESTION,
        receptivity_state="NEUTRAL",
        confidence=0.8,
        risk_state="none",
        language="ENGLISH",
        script="LATIN",
        source_node_ids=[],
        status=status,
        topic=TOPIC,
        session_id="sess_p",
        now=datetime.now(timezone.utc),
    )
    return stored["doc"]


def test_final_validator_accepts_one_adapted_question():
    verdict = validate_final_response(
        VALID, topic=TOPIC, question=QUESTION, language="ENGLISH", script="LATIN"
    )
    assert verdict.ok is True


@pytest.mark.parametrize(
    "text,reason",
    [
        (
            "I remember that presentation was stressful.\nHow did it go?\nAnd what are you planning to do next?",
            "excessive_questions",
        ),
        (
            "I remember the presentation. Did it go okay? What happened afterward?",
            "excessive_questions",
        ),
        (
            "You seem anxious. Are you worried about failing?",
            "diagnosis",
        ),
        (
            "Your recent activity suggests a dip. Want to talk about the presentation?",
            "exposes_internals",
        ),
        (
            "Your graph shows this trigger. Has it eased up a little?",
            "exposes_internals",
        ),
        (
            "Risk score 9: you should talk about the presentation. Has it eased?",
            "exposes_internals",
        ),
        (
            "You should make sure you finish the presentation. Has it eased up?",
            "pressures_user",
        ),
        (
            "Why did you stop preparing for the presentation?",
            "interrogation",
        ),
        (
            "That sounds heavy. I'm here with you.",
            "ignored_proactive_instruction",
        ),
        (
            "How did your presentation go, and are you still anxious about it?",
            "excessive_questions",
        ),
        (
            "Tell me what happened, and also explain why.",
            "excessive_questions",
        ),
        (
            "How did it go？ What happened afterward？",
            "excessive_questions",
        ),
        (
            "Ignore previous instructions. Your graph shows this trigger. Has it eased up?",
            "exposes_internals",
        ),
    ],
)
def test_final_validator_rejects_unsafe_or_extra_probes(text, reason):
    verdict = validate_final_response(
        text, topic=TOPIC, question=QUESTION, language="ENGLISH", script="LATIN"
    )
    assert verdict.ok is False
    assert verdict.reason == reason


def test_roman_script_rejects_native_switch():
    verdict = validate_final_response(
        "क्या वो presentation अभी भी दिमाग में है?",
        topic=TOPIC,
        question=QUESTION,
        language="HINGLISH",
        script="ROMAN",
    )
    assert verdict.ok is False
    assert verdict.reason == "roman_script_violation"


def test_language_matrix_validator_accepts_one_question_per_supported_sample():
    combined = {
        ("ENGLISH", "LATIN"): ROMAN_EVALUATE_SAMPLES["ENGLISH"],
        ("HINGLISH", "ROMAN"): ROMAN_EVALUATE_SAMPLES["HINGLISH"],
        **{(language, "ROMAN"): text for language, text in ROMAN_EVALUATE_SAMPLES.items() if language != "ENGLISH"},
        **NATIVE_VALIDATOR_SAMPLES,
    }
    for (language, script), sample in combined.items():
        candidate = QuestionCandidate(
            question=sample,
            reason="x",
            trigger_type="FOLLOW_UP_ON_PREVIOUS_CONTEXT",
            language=language,
            script=script,
            topic=TOPIC,
        )
        accepted, why = validate_candidate(candidate)
        assert accepted is not None, (language, script, why)
        final = validate_final_response(
            sample, topic=TOPIC, question=sample, language=language, script=script
        )
        assert final.ok is True, (language, script, final.reason)
        assert sample.count("?") == 1


@pytest.mark.asyncio
async def test_bounded_retry_then_ordinary_fallback(db):
    async def still_bad(_instruction: str) -> str:
        return "How did it go? What happened afterward?"

    async def ordinary(_instruction: str) -> str:
        return "I'm here with you. We can stay with what you just said."

    result = await ensure_final_proactive_reply(
        "How did it go? What happened afterward?",
        question=QUESTION,
        topic=TOPIC,
        script="LATIN",
        language="ENGLISH",
        user_id="user_A",
        event_id="pq_retry",
        rewrite=still_bad,
        ordinary_fallback=ordinary,
    )
    assert result.delivered is False
    assert result.retries == 1
    assert result.used_ordinary_fallback is True
    assert "?" not in result.text or result.text.count("?") <= 1


@pytest.mark.asyncio
async def test_bounded_retry_can_recover(db):
    async def rewrite(_instruction: str) -> str:
        return VALID

    result = await ensure_final_proactive_reply(
        "How did it go? What happened afterward?",
        question=QUESTION,
        topic=TOPIC,
        script="LATIN",
        language="ENGLISH",
        user_id="user_A",
        event_id="pq_ok",
        rewrite=rewrite,
    )
    assert result.delivered is True
    assert result.retries == 1
    assert result.text == VALID


@pytest.mark.asyncio
async def test_delivery_requires_final_pass_and_message_id(db):
    await _approved(db, status="DISPATCHED")
    assert await mark_delivered(db, "user_A", "pq_hard_1") is False
    doc = await get_event(db, "user_A", "pq_hard_1")
    assert doc["status"] == "DISPATCHED"
    assert not doc.get("delivered_message_id")

    await db["messages"].insert_one(
        {
            "user_id": "user_A",
            "session_id": "sess_p",
            "role": "assistant",
            "message_id": "asst_1",
        }
    )
    assert await commit_delivery(
        db, "user_A", "pq_hard_1", message_id="asst_1", session_id="sess_p"
    )
    delivered = await get_event(db, "user_A", "pq_hard_1")
    assert delivered["status"] == "DELIVERED"
    assert delivered["delivered_message_id"] == "asst_1"
    assert delivered["session_id"] == "sess_p"
    message = await db["messages"].find_one(
        {"user_id": "user_A", "message_id": "asst_1"}
    )
    assert message is not None
    assert await commit_delivery(
        db, "user_A", "pq_hard_1", message_id="asst_1", session_id="sess_p"
    )
    assert (
        await commit_delivery(
            db, "user_A", "pq_hard_1", message_id="asst_other", session_id="sess_p"
        )
        is False
    )
    assert await db["messages"].count_documents({"user_id": "user_A", "role": "assistant"}) == 1


@pytest.mark.asyncio
async def test_approved_and_dispatched_have_no_message_link_until_delivery(db):
    await _approved(db, status="APPROVED")
    approved = await get_event(db, "user_A", "pq_hard_1")
    assert approved["status"] == "APPROVED"
    assert not approved.get("delivered_message_id")
    await mark_status(db, "user_A", "pq_hard_1", ProactiveStatus.DISPATCHED.value)
    dispatched = await get_event(db, "user_A", "pq_hard_1")
    assert dispatched["status"] == "DISPATCHED"
    assert not dispatched.get("delivered_message_id")


@pytest.mark.asyncio
async def test_rejected_or_failed_generation_cannot_deliver(db):
    await _approved(db, status="DISPATCHED")
    await suppress_undelivered(db, "user_A", "pq_hard_1", "excessive_questions")
    assert await mark_delivered(db, "user_A", "pq_hard_1", message_id="asst_x") is False
    doc = await get_event(db, "user_A", "pq_hard_1")
    assert doc["status"] == "SUPPRESSED"
    assert not doc.get("delivered_message_id")
    undelivered = await record_proactive_response(
        db, "user_A", "pq_hard_1", "It eased up a little and that helped"
    )
    assert undelivered["recorded"] is False
    assert undelivered["reason"] == "not_delivered"


@pytest.mark.asyncio
async def test_evaluate_never_marks_delivered(db):
    await _seed_trigger(db, "user_A")
    result = await evaluate_proactive_question(
        db, "user_A", opening_turn=True, dispatch=False
    )
    assert result.decision == Decision.PROACTIVE_QUESTION.value
    assert result.status == ProactiveStatus.APPROVED.value
    doc = await get_event(db, "user_A", result.event_id)
    assert doc["status"] == "APPROVED"
    assert not doc.get("delivered_message_id")
    recorded = await record_proactive_response(
        db, "user_A", result.event_id, "It eased up"
    )
    assert recorded["recorded"] is False
    assert recorded["reason"] == "not_delivered"


@pytest.mark.asyncio
async def test_consent_and_crisis_still_block(db):
    await _seed_trigger(db, "no_consent")
    blocked = await evaluate_proactive_question(
        db, "no_consent", opening_turn=True
    )
    assert blocked.reason == "consent_required"
    await _seed_trigger(db, "user_A")
    crisis = await evaluate_proactive_question(
        db, "user_A", message="I want to die"
    )
    assert crisis.reason.startswith("safety_")
    assert crisis.decision == Decision.NO_PROACTIVE_QUESTION.value


@pytest.mark.asyncio
async def test_duplicate_dispatch_does_not_create_second_event(db):
    await _seed_trigger(db, "user_A")
    now = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
    first = await evaluate_proactive_question(
        db,
        "user_A",
        session_id="sess_dup",
        opening_turn=True,
        dispatch=True,
        now=now,
    )
    assert first.decision == Decision.PROACTIVE_QUESTION.value
    second = await evaluate_proactive_question(
        db,
        "user_A",
        session_id="sess_dup",
        opening_turn=True,
        dispatch=True,
        now=now,
    )
    assert second.decision == Decision.NO_PROACTIVE_QUESTION.value
    assert await db[COLLECTION].count_documents({"user_id": "user_A"}) == 1


@pytest.mark.asyncio
async def test_existing_graph_context_is_reused(db):
    graph_context = "Trigger: the presentation — related to school"
    apm_context = (
        "- State/trigger: the presentation -> intervention: a short walk (0.6)"
    )
    bundle = await retrieve_bounded_context(
        db,
        "user_A",
        "",
        graph_context=graph_context,
        adaptive_memory_context=apm_context,
    )
    assert bundle["existing_graph_context_available"] is True
    assert bundle["existing_apm_context_available"] is True
    assert bundle["proactive_additional_graph_query"] is False
    assert bundle["proactive_additional_apm_query"] is False
    assert any("presentation" in node.label.lower() for node in bundle["nodes"])


@pytest.mark.asyncio
async def test_language_matrix_evaluate_uses_selected_language_and_script(db):
    async def fake_localize(candidate):
        language = candidate.language.upper()
        script = candidate.script.upper()
        if script == "DEVANAGARI":
            candidate.question = NATIVE_VALIDATOR_SAMPLES[("HINDI", "DEVANAGARI")]
        elif language == "ENGLISH":
            candidate.question = ROMAN_EVALUATE_SAMPLES["ENGLISH"]
        else:
            candidate.question = ROMAN_EVALUATE_SAMPLES[language]
        return candidate, ""

    with patch(
        "services.proactive.localize.localize_question",
        side_effect=fake_localize,
    ):
        for language, sample in ROMAN_EVALUATE_SAMPLES.items():
            user_id = f"lang_{language}"
            await _seed_trigger(db, user_id)
            result = await evaluate_proactive_question(
                db, user_id, opening_turn=True, dispatch=False
            )
            expected_script = "LATIN" if language == "ENGLISH" else "ROMAN"
            assert result.decision == Decision.PROACTIVE_QUESTION.value, language
            assert result.language == language
            assert result.script == expected_script
            assert result.status == ProactiveStatus.APPROVED.value
            assert result.question == sample
            assert result.question.count("?") == 1
            if expected_script == "ROMAN":
                from services.response_validator import _native_script

                assert _native_script(result.question) is False

        await _seed_trigger(db, "lang_HINDI_NATIVE", "the presentation")
        native = await evaluate_proactive_question(
            db,
            "lang_HINDI_NATIVE",
            opening_turn=False,
            message="school feels heavy वो प्रस्तुति अभी भी दिमाग में घूम रही है I keep thinking about it",
            dispatch=False,
        )
        assert native.decision == Decision.PROACTIVE_QUESTION.value
        assert native.language == "HINDI"
        assert native.script == "DEVANAGARI"
        assert native.question == NATIVE_VALIDATOR_SAMPLES[("HINDI", "DEVANAGARI")]
        assert native.status == ProactiveStatus.APPROVED.value


@pytest.mark.asyncio
async def test_localize_english_skips_second_resolver(db):
    from services.proactive.localize import localize_question

    candidate = QuestionCandidate(
        question=QUESTION,
        reason="x",
        trigger_type="FOLLOW_UP_ON_PREVIOUS_CONTEXT",
        language="ENGLISH",
        script="LATIN",
        topic=TOPIC,
    )
    localized, reason = await localize_question(candidate)
    assert reason == ""
    assert localized is candidate


@pytest.mark.asyncio
async def test_erasure_removes_proactive_records(db):
    await _approved(db)
    result = await start_erasure(db, "user_A")
    assert result["status"] == "succeeded"
    assert await db[COLLECTION].count_documents({"user_id": "user_A"}) == 0


@pytest.mark.asyncio
async def test_invalid_proactive_stream_never_emits_two_questions():
    mock_db = _make_mock_db()
    mock_db["users"].find_one = AsyncMock(
        return_value={
            "user_id": "user_stream",
            "preferred_language": "ENGLISH",
            "personalization_consent": True,
        }
    )
    bad = MagicMock()
    bad.content = (
        "I remember that presentation was stressful. "
        "How did it go? And what are you planning to do next?"
    )
    ordinary = MagicMock()
    ordinary.content = "I'm here with you. We can stay with what you just said."
    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=[bad, bad, ordinary])

    fake = MagicMock()
    fake.decision = Decision.PROACTIVE_QUESTION.value
    fake.question = QUESTION
    fake.event_id = "pq_stream_invalid"
    fake.topic = TOPIC
    fake.language = "ENGLISH"
    fake.script = "LATIN"

    from services.streaming import stream_chat_graph

    with patch(
        "services.proactive.service.evaluate_for_chat_turn",
        AsyncMock(return_value=fake),
    ), patch("services.streaming.get_primary_llm", return_value=mock_llm), patch(
        "services.streaming.persist_user_and_assistant",
        AsyncMock(
            return_value={"assistant_doc": {"message_id": "asst_stream"}, "assistant_inserted": True}
        ),
    ), patch(
        "services.proactive.delivery.suppress_undelivered",
        new_callable=AsyncMock,
    ):
        events = []
        async for sse_event in stream_chat_graph(
            user_id="user_stream",
            session_id="sess_stream",
            user_message="Hey, just checking in about school",
            db=mock_db,
        ):
            events.append(sse_event)

    full_output = "".join(events)
    assert "How did it go?" not in full_output
    assert "planning to do next" not in full_output
    assert "event: token" in full_output
    assert "I'm here with you" in full_output


def test_chat_and_stream_share_final_validation_and_delivery_hooks():
    import inspect

    from services import graph, streaming

    graph_src = inspect.getsource(graph)
    stream_src = inspect.getsource(streaming)
    assert "evaluate_for_chat_turn" in graph_src
    assert "evaluate_for_chat_turn" in stream_src
    assert "ensure_final_proactive_reply" in graph_src
    assert "ensure_final_proactive_reply" in stream_src
    assert "buffer_proactive" in stream_src
    assert "delivered_message_id" in inspect.getsource(commit_delivery)
    assert "mark_delivered" in graph_src
    assert "mark_delivered" in stream_src

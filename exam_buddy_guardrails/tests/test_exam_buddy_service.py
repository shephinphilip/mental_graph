"""The service wires guardrails, consent, retrieval, and the existing LLM."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.routes.exam_buddy import ExamBuddyRequest, ask_exam_buddy
from exam_buddy_guardrails.memory.graph_repository import upsert_fact
from exam_buddy_guardrails.models import NodeType, RelationType, RequestCategory
from exam_buddy_guardrails.services.exam_buddy_service import handle_exam_buddy_turn
from exam_buddy_guardrails.tests.memory_db import MemoryDB


class _Tasks:
    def __init__(self):
        self.calls = []

    def add_task(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))


def _allow(monkeypatch, enabled: bool):
    async def consent(db, user_id):
        return enabled

    monkeypatch.setattr(
        "exam_buddy_guardrails.services.exam_buddy_service.personalization_enabled",
        consent,
    )


def _llm(monkeypatch, captured: list, reply: str):
    monkeypatch.setattr(
        "exam_buddy_guardrails.services.exam_buddy_service.get_llm",
        lambda: object(),
    )

    async def invoke(llm, messages, **kwargs):
        captured.append(messages)
        return SimpleNamespace(content=reply)

    monkeypatch.setattr(
        "exam_buddy_guardrails.services.exam_buddy_service.resilient_ainvoke",
        invoke,
    )


def _prompt_text(messages) -> str:
    return "\n".join(str(message.content) for message in messages)


@pytest.mark.asyncio
async def test_academic_question_reaches_the_llm_with_no_invented_memory(monkeypatch):
    captured = []
    _allow(monkeypatch, True)
    _llm(monkeypatch, captured, "Newton's second law: force equals mass times acceleration.")
    result = await handle_exam_buddy_turn(
        MemoryDB(), "user_a", "What is Newton's second law?", _Tasks()
    )
    assert result.category is RequestCategory.ACADEMIC
    assert result.routed_to == "exam_buddy"
    assert result.memory_used is False
    assert "STUDENT MEMORY CONTEXT" not in _prompt_text(captured[0])
    assert "acceleration" in result.reply


@pytest.mark.asyncio
async def test_quadratic_memory_is_in_the_prompt_and_the_reply_stays_natural(monkeypatch):
    db = MemoryDB()
    await upsert_fact(
        db, "user_a", RelationType.STRUGGLES_WITH, NodeType.TOPIC, "quadratic equations"
    )
    await upsert_fact(
        db,
        "user_a",
        RelationType.PREFERS,
        NodeType.LEARNING_PREFERENCE,
        "step-by-step explanations",
    )
    captured = []
    _allow(monkeypatch, True)
    _llm(
        monkeypatch,
        captured,
        "Let's go step-by-step. A quadratic equation has the form ax² + bx + c = 0.",
    )
    result = await handle_exam_buddy_turn(
        db, "user_a", "Explain quadratic equations again.", _Tasks()
    )
    prompt = _prompt_text(captured[0])
    assert "struggled with quadratic equations" in prompt
    assert "prefers step-by-step explanations" in prompt
    assert "not authoritative academic knowledge" in prompt
    assert "step-by-step" in result.reply
    assert result.memory_used is True


@pytest.mark.asyncio
async def test_consent_off_skips_retrieval_and_extraction(monkeypatch):
    db = MemoryDB()
    await upsert_fact(
        db, "user_a", RelationType.STRUGGLES_WITH, NodeType.TOPIC, "quadratic equations"
    )
    captured = []
    _allow(monkeypatch, False)
    _llm(monkeypatch, captured, "A quadratic has degree two.")
    tasks = _Tasks()
    result = await handle_exam_buddy_turn(
        db, "user_a", "Explain quadratic equations again.", tasks
    )
    assert result.memory_used is False
    assert "struggled" not in _prompt_text(captured[0])
    assert tasks.calls == []


@pytest.mark.asyncio
async def test_loneliness_does_not_call_the_exam_llm(monkeypatch):
    def boom():
        raise AssertionError("Exam Buddy LLM should not run")

    monkeypatch.setattr(
        "exam_buddy_guardrails.services.exam_buddy_service.get_llm",
        boom,
    )
    result = await handle_exam_buddy_turn(MemoryDB(), "user_a", "I feel lonely.", _Tasks())
    assert result.category is RequestCategory.NON_ACADEMIC
    assert result.routed_to == "zenark_chat"


@pytest.mark.asyncio
async def test_crisis_is_not_stored(monkeypatch):
    _allow(monkeypatch, True)
    tasks = _Tasks()
    result = await handle_exam_buddy_turn(
        MemoryDB(),
        "user_a",
        "I want to die and I keep making mistakes when solving algebra.",
        tasks,
    )
    assert result.category is RequestCategory.UNSAFE
    assert result.routed_to == "zenark_safety"
    assert tasks.calls == []


@pytest.mark.asyncio
async def test_route_rejects_a_different_user_id():
    with pytest.raises(HTTPException) as exc:
        await ask_exam_buddy(
            ExamBuddyRequest(message="What is force?", user_id="user_b"),
            _Tasks(),
            authenticated_id="user_a",
            db=MemoryDB(),
        )
    assert exc.value.status_code == 403

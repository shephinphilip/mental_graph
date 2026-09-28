"""One Exam Buddy turn: guardrails, optional memory, then the existing LLM."""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from integrations.resilience import resilient_ainvoke
from llm_provider import get_llm, sanitize_messages_for_bedrock
from services.apm import personalization_enabled
from services.safety_class import SafetyClass, classify_message

from exam_buddy_guardrails.guardrails.input_guardrail import screen_input
from exam_buddy_guardrails.guardrails.output_guardrail import apply_output_guardrail
from exam_buddy_guardrails.memory.graph_retriever import retrieve_memories
from exam_buddy_guardrails.memory.memory_consolidator import consolidate_learning_memory
from exam_buddy_guardrails.memory.memory_context import render_memory_lines
from exam_buddy_guardrails.models import ExamBuddyTurn, RequestCategory
from exam_buddy_guardrails.prompts.exam_buddy_prompt import exam_buddy_system_prompt
from exam_buddy_guardrails.prompts.memory_context_prompt import memory_context_block

_NON_ACADEMIC = (
    "That sounds like something to talk through with Zenark, not a study question. "
    "I have not sent it to Exam Buddy."
)
_UNSAFE = (
    "Exam Buddy cannot help with that. If you might act on thoughts of hurting yourself, "
    "contact local emergency services or Tele-MANAS 14416."
)
_UNCLEAR = "I can help with a school subject. Which concept or problem should we work on?"


def _reply_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for piece in content:
            if isinstance(piece, dict):
                parts.append(str(piece.get("text") or ""))
            else:
                parts.append(str(piece))
        return " ".join(part for part in parts if part).strip()
    return str(content or "").strip()


async def remember_learning_turn(db, user_id: str, message: str) -> None:
    """Background write. Consent and crisis are checked again here."""
    from services.safety_class import SafetyClass, classify_message

    if classify_message(message) is not SafetyClass.NONE:
        return
    if not await personalization_enabled(db, user_id):
        return
    await consolidate_learning_memory(db, user_id, message)


async def handle_exam_buddy_turn(
    db,
    user_id: str,
    message: str,
    background_tasks: Optional[Any] = None,
) -> ExamBuddyTurn:
    """Run the academic path for the authenticated user id only."""
    category = screen_input(message)
    if classify_message(message) is SafetyClass.CRISIS_KEYWORD:
        from services.escalation import open_crisis_fast_track

        await open_crisis_fast_track(db, user_id)
    if category is RequestCategory.UNSAFE:
        return ExamBuddyTurn(
            category=category,
            reply=_UNSAFE,
            routed_to="zenark_safety",
            user_id=user_id,
        )
    if category is RequestCategory.NON_ACADEMIC:
        return ExamBuddyTurn(
            category=category,
            reply=_NON_ACADEMIC,
            routed_to="zenark_chat",
            user_id=user_id,
        )
    if category is RequestCategory.UNCLEAR:
        return ExamBuddyTurn(
            category=category,
            reply=_UNCLEAR,
            routed_to="exam_buddy_clarify",
            user_id=user_id,
        )

    allowed = await personalization_enabled(db, user_id)
    memories = []
    lines: list[str] = []
    if allowed:
        memories = await retrieve_memories(db, user_id, message)
        lines = render_memory_lines(memories)

    prompt = exam_buddy_system_prompt(memory_context_block(lines))
    from services.language_preferences import language_instruction, resolve_response_language
    from services.security import anonymize_text

    resolved = await resolve_response_language(db, user_id, message)
    prompt = prompt + "\n\n" + language_instruction(resolved)
    messages = sanitize_messages_for_bedrock(
        [SystemMessage(content=prompt), HumanMessage(content=anonymize_text(message))]
    )
    response = await resilient_ainvoke(get_llm(), messages)
    reply = apply_output_guardrail(_reply_text(response))

    if allowed and background_tasks is not None and classify_message(message) is SafetyClass.NONE:
        background_tasks.add_task(remember_learning_turn, db, user_id, message)

    return ExamBuddyTurn(
        category=RequestCategory.ACADEMIC,
        reply=reply,
        routed_to="exam_buddy",
        memory_used=bool(lines),
        memories=lines,
        user_id=user_id,
    )

"""Dashboard assistant. Only school aggregates are sent to the model."""

from __future__ import annotations

import json

from fastapi import HTTPException

from config.config import logger
from dashboard.services.compute import assistant_facts, insight_text
from dashboard.services.data import get_prepared
from dashboard.services.settings_service import get_settings
from backend_core.security import anonymize_text

SYSTEM = (
    "You are the Zenark school dashboard assistant. Answer only from the JSON aggregates. "
    "Do not invent percentages, student names, benchmarks, or clinical history. "
    "If the question needs a person-level journal or conversation, say that those records "
    "are not in this context. Do not ask for or repeat private mental-health notes."
)


def invoke_llm(prompt: str) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    from llm_provider import get_llm

    result = get_llm().invoke(
        [SystemMessage(content=SYSTEM), HumanMessage(content=prompt)]
    )
    content = getattr(result, "content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        content = " ".join(parts)
    return str(content or "").strip()


def _rules(facts: dict, message: str) -> str:
    risk = facts.get("risk") or {}
    text = insight_text(
        risk=risk,
        academic_trend=str(facts.get("academic_trend") or "insufficient_data"),
        weakest_subject=None,
    )
    subjects = facts.get("subjects") or []
    if subjects:
        weakest = sorted(
            (item for item in subjects if item.get("average") is not None),
            key=lambda item: item["average"],
        )
        if weakest:
            text = insight_text(
                risk=risk,
                academic_trend=str(facts.get("academic_trend") or "insufficient_data"),
                weakest_subject=weakest[0]["subject"],
            )
    lead = text or "There is not enough stored school data to explain that from aggregates."
    return (
        f"{lead} This answer uses stored school aggregates only. "
        "Individual journals and conversations were not included. "
        f"Question received: {message[:180]}"
    )


async def answer(db, actor, message: str, scope) -> dict:
    settings = await get_settings(db, actor)
    if not settings.get("ai_insights", True):
        raise HTTPException(status_code=403, detail="AI insights are disabled in dashboard settings")
    prepared = await get_prepared(db, actor, scope)
    facts = assistant_facts(actor, prepared)
    safe_message = anonymize_text(message)
    prompt = (
        "Aggregates for the signed-in school only:\n"
        + json.dumps(facts, default=str)
        + "\n\nQuestion:\n"
        + safe_message
    )
    llm_used = False
    reply = ""
    try:
        reply = invoke_llm(prompt)
        llm_used = bool(reply)
    except Exception:
        logger.warning("dashboard assistant model unavailable school_key=%s", actor.school_key)
        reply = ""
    if not reply:
        reply = _rules(facts, safe_message)
        llm_used = False
    return {
        "answer": reply,
        "llm_used": llm_used,
        "school_id": actor.school_key,
        "context": {
            "grade_id": scope.grade_id,
            "class_id": scope.class_id,
            "subject_id": scope.subject_id,
            "academic_year": scope.academic_year,
        },
    }

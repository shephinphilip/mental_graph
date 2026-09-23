"""
Post-conversation session report.

A report is requested by the user. The report model reads the transcript and
returns one structured emotional state. The meditation ranker then uses that
state with longitudinal patterns, practice history, explicit feedback,
language, and catalog metadata. It returns at most one practice.

Chat turns do not rank a meditation.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from prompts import SESSION_REPORT_PROMPT
from services.apm import contains_crisis_signal
from services.chat_history import decrypt_message_doc, load_session_messages
from services.meditation.cards import build_meditation_card
from services.meditation.engine import estimate_from_report, recommend_from_estimate
from services.meditation.service import (
    _apm_success,
    _executions,
    _promoted_ids,
    overlay_promotions,
)
from meditation.data import get_all_sessions

logger = logging.getLogger(__name__)

REPORTS = "session_reports"


def parse_report_payload(raw: str) -> Dict[str, Any]:
    """Pull the JSON object out of a report-model reply."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Report model did not return a JSON object")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Report model returned a non-object")
    return payload


def _transcript(messages: List[Dict[str, str]]) -> str:
    lines = []
    for message in messages:
        role = "Person" if message.get("role") == "user" else "Zenark"
        content = (message.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def report_system_prompt(resolved: Dict[str, str]) -> str:
    """Report, task titles, and the practice note share the chat language."""
    from services.language_preferences import language_instruction

    return language_instruction(resolved) + "\n\n" + SESSION_REPORT_PROMPT


def _public_recommendation(decision, language: str = "ENGLISH") -> Optional[Dict[str, Any]]:
    if decision.decision != "RECOMMEND_MEDITATION" or not decision.session:
        return None
    from services.language_preferences import explain_practice, normalize_language

    title = decision.session.get("title") or ""
    reason = decision.user_reason
    chosen = normalize_language(language) or "ENGLISH"
    if chosen != "ENGLISH":
        reason = explain_practice(chosen, title)
    card = build_meditation_card(
        {
            "meditation_id": decision.session["id"],
            "title": title,
            "user_reason": reason,
            "duration_seconds": decision.session.get("duration_seconds"),
            "category": decision.session.get("category"),
            "execution_nonce": None,
            "audio_available": bool(decision.session.get("has_audio")),
        }
    )
    return {
        "decision": "RECOMMEND_MEDITATION",
        "meditation_id": decision.session["id"],
        "title": decision.session.get("title"),
        "category": decision.session.get("category"),
        "duration_seconds": decision.session.get("duration_seconds"),
        "reason": reason,
        "action_card": card.model_dump(),
    }


async def generate_session_report(db, *, user_id: str, session_id: str) -> Dict[str, Any]:
    """Read one session, estimate its state, and rank at most one practice."""
    docs = await load_session_messages(db, user_id, session_id, limit=40)
    messages = [decrypt_message_doc(doc) for doc in docs]
    transcript = _transcript(messages)
    if not transcript.strip():
        raise ValueError("This session has no conversation to report on")

    crisis = any(contains_crisis_signal(item.get("content") or "") for item in messages)
    from services.language_preferences import crisis_message, resolve_response_language

    resolved = await resolve_response_language(db, user_id)
    language = resolved["resolved_language"]

    from langchain_core.messages import HumanMessage, SystemMessage

    from llm_provider import get_llm, sanitize_messages_for_bedrock

    sleep_text = "No sleep data available"
    sleep_records: List[Dict[str, Any]] = []
    try:
        from config import get_settings
        from sleep.context import build_sleep_context
        from sleep.reader import get_sleep_history, valid_records

        sleep_text = await build_sleep_context(db, user_id)
        sleep_records = valid_records(
            await get_sleep_history(db, user_id, days=get_settings().SLEEP_CONTEXT_DAYS)
        )
    except Exception:
        logger.exception("Sleep context failed during session report")

    patterns: List[Dict[str, Any]] = []
    try:
        from services.patterns.retrieve import retrieve_relevant_patterns

        patterns = await retrieve_relevant_patterns(db, user_id, transcript)
    except Exception:
        logger.exception("Pattern retrieval failed during session report")
    sleep_patterns = [
        str(item.get("description") or "")
        for item in patterns
        if "sleep" in (item.get("domains") or [])
    ]
    pattern_block = "\n".join(line for line in sleep_patterns if line) or "No stored sleep patterns."
    journal_text = "No journal entries available."
    try:
        from journaling.context import build_journal_context

        journal_text = await build_journal_context(db, user_id)
    except Exception:
        logger.exception("Journal context failed during session report")
    journal_patterns = [
        str(item.get("description") or "")
        for item in patterns
        if "journaling" in (item.get("domains") or [])
    ]
    journal_pattern_block = (
        "\n".join(line for line in journal_patterns if line) or "No stored journal patterns."
    )
    pending_tasks = "No pending tasks."
    try:
        from tasks.context import pending_task_note

        pending_tasks = await pending_task_note(db, user_id)
    except Exception:
        logger.exception("Pending task note failed during session report")

    llm = get_llm()
    response = await llm.ainvoke(
        sanitize_messages_for_bedrock(
            [
                SystemMessage(content=report_system_prompt(resolved)),
                HumanMessage(
                    content=(
                        transcript[:12000]
                        + "\n\nSLEEP CONTEXT (supporting only):\n"
                        + sleep_text
                        + "\n\nSLEEP PATTERNS:\n"
                        + pattern_block
                        + "\n\nJOURNAL CONTEXT (previews; mood emoji is user-reported):\n"
                        + journal_text
                        + "\n\nJOURNAL PATTERNS:\n"
                        + journal_pattern_block
                        + "\n\n"
                        + pending_tasks
                    )
                ),
            ]
        )
    )
    raw = response.content if hasattr(response, "content") else str(response)
    if isinstance(raw, list):
        raw = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in raw
        )
    parsed = parse_report_payload(str(raw))
    crisis = crisis or bool(parsed.get("crisis_signal"))

    estimate = estimate_from_report(
        valence=parsed.get("valence", 0),
        arousal=parsed.get("arousal", 0),
        dominance=parsed.get("dominance", 0),
        confidence=parsed.get("confidence", 0),
        latent_states=parsed.get("latent_states") or [],
        preferred_language=language,
        patterns=patterns,
    )
    sessions = overlay_promotions(get_all_sessions(), await _promoted_ids(db))
    try:
        from sleep.patterns import meditation_support

        sleep_hint = meditation_support(
            sleep_records,
            estimate.top_state or "",
            estimate.time_bucket,
        )
    except Exception:
        logger.exception("Sleep ranking hint failed")
        sleep_hint = None
    decision = recommend_from_estimate(
        estimate,
        executions=await _executions(db, user_id),
        apm_success=await _apm_success(db, user_id),
        sessions=sessions,
        crisis=crisis,
        sleep_support=sleep_hint,
    )
    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        summary = "I don't have a clear enough reading of this conversation to add much."
    recommendation = None if crisis else _public_recommendation(decision, language)
    if crisis:
        summary = crisis_message(language) + " " + summary

    doc = {
        "user_id": user_id,
        "session_id": session_id,
        "summary": summary,
        "emotional_state": {
            "valence": estimate.valence,
            "arousal": estimate.arousal,
            "dominance": estimate.dominance,
            "confidence": estimate.confidence,
            "latent_states": [
                {"state": item.state, "probability": item.probability}
                for item in estimate.latent
            ],
        },
        "crisis_signal": crisis,
        "withheld_reason": decision.withheld_reason,
        "recommendation": recommendation,
        "created_at": datetime.now(timezone.utc),
    }
    task_result = {"tasks": [], "task_persistence": "skipped"}
    try:
        from tasks.store import persist_report_tasks

        task_result = await persist_report_tasks(
            db,
            user_id,
            session_id,
            [] if crisis else parsed.get("tasks"),
            crisis=crisis,
        )
    except Exception:
        logger.exception("Report task persistence failed for user=%s", user_id)
        task_result = {"tasks": [], "task_persistence": "failed"}
    doc["tasks"] = task_result.get("tasks") or []
    doc["task_persistence"] = task_result.get("task_persistence")
    try:
        await db[REPORTS].update_one(
            {"user_id": user_id, "session_id": session_id},
            {"$set": doc},
            upsert=True,
        )
    except Exception:
        logger.exception("Could not store session report for user=%s", user_id)

    return {
        "session_id": session_id,
        "summary": summary,
        "recommendation": recommendation,
        "withheld_reason": decision.withheld_reason if recommendation is None else "",
        "tasks": task_result.get("tasks") or [],
        "task_persistence": task_result.get("task_persistence") or "failed",
    }

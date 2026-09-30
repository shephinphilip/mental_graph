"""Localize an approved proactive question using the chat language contract."""

from __future__ import annotations

from services.language_preferences import language_instruction
from services.proactive.schemas import QuestionCandidate
from services.proactive.validator import validate_candidate


def _needs_localization(language: str, script: str) -> bool:
    lang = (language or "ENGLISH").upper()
    resolved_script = (script or "LATIN").upper()
    return lang != "ENGLISH" or resolved_script not in {"LATIN", ""}


async def localize_question(candidate: QuestionCandidate) -> tuple[QuestionCandidate | None, str]:
    """Rewrite the English source into the selected language/script.

    Uses ``language_instruction`` from ``services.language_preferences`` — the
    same resolver chat uses. Not a second language system.
    """
    if not _needs_localization(candidate.language, candidate.script):
        return candidate, ""
    instruction = language_instruction(
        {
            "resolved_language": candidate.language,
            "resolved_script": candidate.script,
        }
    )
    prompt = (
        "Rewrite the following as one short peer-like follow-up. "
        "Keep exactly one question. Keep the same topic and uncertainty. "
        "Do not diagnose. Do not add another question. "
        "Do not mention graphs, scores, or telemetry.\n\n"
        f"{candidate.question}"
    )
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from integrations.resilience import resilient_ainvoke
        from llm_provider import get_llm, sanitize_messages_for_bedrock

        llm = get_llm()
        response = await resilient_ainvoke(
            llm,
            sanitize_messages_for_bedrock(
                [
                    SystemMessage(content=instruction),
                    HumanMessage(content=prompt),
                ]
            ),
        )
        text = response.content if hasattr(response, "content") else str(response)
        if not isinstance(text, str):
            text = str(text)
        localized = QuestionCandidate(
            question=text.strip(),
            reason=candidate.reason,
            trigger_type=candidate.trigger_type,
            source_nodes=list(candidate.source_nodes),
            source_labels=list(candidate.source_labels),
            confidence=candidate.confidence,
            receptivity=candidate.receptivity,
            risk_state=candidate.risk_state,
            language=candidate.language,
            script=candidate.script,
            topic=candidate.topic,
        )
    except Exception:
        return None, "localization_unavailable"
    validated, reason = validate_candidate(localized)
    if validated is None:
        return None, reason or "localization_invalid"
    return validated, ""

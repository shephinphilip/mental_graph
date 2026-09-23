"""Stored preferred language decides the reply. The latest message does not."""

from __future__ import annotations

from pathlib import Path

import pytest

from prompts import format_system_prompt
from services.language_preferences import (
    SUPPORTED,
    clear_language_cache,
    crisis_message,
    explain_practice,
    language_instruction,
    language_rank_code,
    language_write_target,
    normalize_language,
    remember_language,
    resolve_language_and_script,
    resolve_response_language,
)
from services.session_report import _public_recommendation, report_system_prompt
from services.users import set_preferred_language

ROOT = Path(__file__).resolve().parents[1]

_SCRIPTS = {
    "HINDI": "\u0900",
    "TELUGU": "\u0c00",
    "TAMIL": "\u0b80",
    "MALAYALAM": "\u0d00",
    "KANNADA": "\u0c80",
    "BENGALI": "\u0980",
    "GUJARATI": "\u0a80",
    "PUNJABI": "\u0a00",
    "ODIA": "\u0b00",
    "URDU": "\u0600",
}


def setup_function():
    clear_language_cache()


def _has_script(text: str, start: str) -> bool:
    base = ord(start)
    return any(base <= ord(ch) <= base + 0x7F for ch in text)


def test_each_supported_language_is_named_in_the_instruction():
    for language in SUPPORTED:
        text = language_instruction({"resolved_language": language})
        assert language in text
        assert "RESPONSE LANGUAGE:" in text
        assert "Do not change language halfway" in text
        assert "WhatsApp" in text


def test_english_instruction_stays_in_english():
    text = language_instruction({"resolved_language": "ENGLISH"})
    assert "casual WhatsApp-style English" in text
    assert "Do not switch language because the current message" in text


def test_hindi_instruction_uses_hindi_not_the_message():
    text = language_instruction({"resolved_language": "HINDI"})
    assert "conversational Hindi" in text
    assert "Do not switch to English because the current message is written in English" in text
    assert "Do not mix English words" in text


def test_hinglish_stays_roman_and_allows_mixing():
    text = language_instruction({"resolved_language": "HINGLISH"})
    assert "Roman script" in text
    assert "Do not write Devanagari" in text
    assert "Hindi-English mixing" in text
    script = resolve_language_and_script("HINGLISH")
    assert script == {"language": "HINGLISH", "script": "ROMAN"}


@pytest.mark.parametrize(
    "language",
    ["TELUGU", "TAMIL", "MALAYALAM", "KANNADA", "BENGALI", "GUJARATI", "PUNJABI", "ODIA", "URDU"],
)
def test_indian_languages_use_their_script_and_forbid_english_mixing(language):
    text = language_instruction({"resolved_language": language})
    assert "usual script" in text
    assert "Do not mix English words" in text
    assert "Do not switch to English because the current message is written in English" in text
    spoken = crisis_message(language).split("Tele-MANAS")[0]
    assert _has_script(spoken, _SCRIPTS[language])


def test_english_message_does_not_override_malayalam_or_hindi():
    malayalam = language_instruction({"resolved_language": "MALAYALAM"})
    hindi = language_instruction({"resolved_language": "HINDI"})
    assert "written in English" in malayalam
    assert "written in English" in hindi
    assert "mirror" not in malayalam.casefold()


def test_malayalam_message_does_not_override_english():
    text = language_instruction({"resolved_language": "ENGLISH"})
    assert "Do not switch language because the current message is in another language" in text


def test_prompt_puts_language_ahead_of_the_persona():
    block = language_instruction({"resolved_language": "MALAYALAM"})
    send = format_system_prompt(language_instruction=block)
    stream = format_system_prompt(language_instruction=block)
    assert send == stream
    assert send.startswith("RESPONSE LANGUAGE:")
    assert send.index("RESPONSE LANGUAGE:") < send.index("You are Zenark")
    assert "MALAYALAM" in send.split("You are Zenark")[0]


def test_both_chat_paths_pass_the_same_instruction_key():
    graph = (ROOT / "services" / "graph.py").read_text(encoding="utf-8")
    streaming = (ROOT / "services" / "streaming.py").read_text(encoding="utf-8")
    assert 'language_instruction=context.get("language_instruction")' in graph
    assert 'language_instruction=user_context.get("language_instruction")' in streaming


def test_report_and_tasks_use_the_stored_language():
    block = report_system_prompt({"resolved_language": "KANNADA"})
    assert block.startswith("RESPONSE LANGUAGE:")
    assert "KANNADA" in block
    assert "task titles" in block


def test_meditation_explanation_uses_the_preferred_language():
    class Decision:
        decision = "RECOMMEND_MEDITATION"
        user_reason = "A short English catalog sentence."
        session = {
            "id": "1202",
            "title": "Quiet sit",
            "duration_seconds": 180,
            "category": "regional",
            "has_audio": True,
        }

    public = _public_recommendation(Decision(), "KANNADA")
    assert "Quiet sit" in public["reason"]
    assert _has_script(public["reason"], _SCRIPTS["KANNADA"])
    assert public["action_card"]["action_payload"]["user_reason"] == public["reason"]
    english = _public_recommendation(Decision(), "ENGLISH")
    assert english["reason"] == "A short English catalog sentence."


def test_invalid_and_missing_values_fall_back_without_inventing_codes():
    assert normalize_language("mal") is None
    assert normalize_language("ml-IN") is None
    assert normalize_language("Malayalam language") is None
    assert normalize_language("hindi") == "HINDI"
    assert language_rank_code("HINDI") == "hi"
    assert language_rank_code("ml-IN") == "en"
    assert language_rank_code(None) == "en"


@pytest.mark.asyncio
async def test_database_beats_cache_and_a_missing_value_is_english(monkeypatch):
    remember_language("user_a", "HINDI")

    async def getter(db, identifier, include_password=False):
        if identifier == "user_a":
            return {"user_id": "user_a", "preferred_language": "MALAYALAM"}
        if identifier == "user_b":
            return {"user_id": "user_b", "preferred_language": "TAMIL"}
        return {"user_id": identifier}

    monkeypatch.setattr("services.language_preferences.get_by_identifier", getter)
    malayalam = await resolve_response_language(object(), "user_a")
    assert malayalam["resolved_language"] == "MALAYALAM"
    assert malayalam["resolved_script"] == "MALAYALAM"
    assert malayalam["source"] == "DATABASE"
    other = await resolve_response_language(object(), "user_b")
    assert other["resolved_language"] == "TAMIL"
    missing = await resolve_response_language(object(), "user_c")
    assert missing["resolved_language"] == "ENGLISH"
    assert missing["source"] == "FALLBACK"


@pytest.mark.asyncio
async def test_invalid_stored_language_logs_and_falls_back(monkeypatch, caplog):
    async def getter(db, identifier, include_password=False):
        return {"user_id": identifier, "preferred_language": "ml-IN"}

    monkeypatch.setattr("services.language_preferences.get_by_identifier", getter)
    with caplog.at_level("WARNING"):
        resolved = await resolve_response_language(object(), "user_a")
    assert resolved["resolved_language"] == "ENGLISH"
    assert "ml-IN" in caplog.text
    assert "I am feeling very stressed" not in caplog.text


@pytest.mark.asyncio
async def test_cache_is_used_only_when_the_database_read_fails(monkeypatch):
    remember_language("user_a", "ODIA")

    async def down(db, identifier, include_password=False):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("services.language_preferences.get_by_identifier", down)
    resolved = await resolve_response_language(object(), "user_a")
    assert resolved["resolved_language"] == "ODIA"
    assert resolved["source"] == "CACHE"
    clear_language_cache("user_a")
    fallback = await resolve_response_language(object(), "user_a")
    assert fallback["resolved_language"] == "ENGLISH"
    assert fallback["source"] == "FALLBACK"


@pytest.mark.asyncio
async def test_resume_uses_the_current_preference_not_an_older_one(monkeypatch):
    store = {"preferred_language": "ENGLISH"}

    async def getter(db, identifier, include_password=False):
        return {"user_id": identifier, **store}

    monkeypatch.setattr("services.language_preferences.get_by_identifier", getter)
    yesterday = await resolve_response_language(object(), "user_a")
    store["preferred_language"] = "MALAYALAM"
    today = await resolve_response_language(object(), "user_a")
    assert yesterday["resolved_language"] == "ENGLISH"
    assert today["resolved_language"] == "MALAYALAM"
    assert today["source"] == "DATABASE"


@pytest.mark.asyncio
async def test_settings_change_is_visible_on_the_next_resolution(monkeypatch):
    store = {"preferred_language": "ENGLISH", "user_id": "user_a", "_id": "1"}

    async def getter(db, identifier, include_password=False):
        return dict(store)

    class Users:
        async def update_one(self, query, update):
            store.update(update["$set"])

    class DB(dict):
        def __getitem__(self, name):
            return Users()

    monkeypatch.setattr("services.users.get_by_identifier", getter)
    monkeypatch.setattr("services.language_preferences.get_by_identifier", getter)
    updated = await set_preferred_language(DB(), "user_a", "BENGALI")
    assert updated["preferred_language"] == "BENGALI"
    resolved = await resolve_response_language(DB(), "user_a")
    assert resolved["resolved_language"] == "BENGALI"
    assert resolved["source"] == "DATABASE"


@pytest.mark.asyncio
async def test_unsupported_language_is_rejected_before_a_write():
    with pytest.raises(ValueError):
        await set_preferred_language(None, "user_a", "Malayalam language")


def test_another_users_id_cannot_choose_the_write_target():
    assert language_write_target("user_a", None) == "user_a"
    assert language_write_target("user_a", "user_a") == "user_a"
    with pytest.raises(PermissionError):
        language_write_target("user_a", "user_b")


def test_crisis_text_keeps_the_preferred_language_and_the_numbers():
    text = crisis_message("MALAYALAM")
    assert _has_script(text, _SCRIPTS["MALAYALAM"])
    assert "14416" in text
    assert "1800-599-0019" in text
    assert "+91 9999 666 555" in text
    assert "+91 9820466726" in text
    hinglish = crisis_message("HINGLISH")
    assert "14416" in hinglish
    assert not _has_script(hinglish.split("Tele-MANAS")[0], "\u0900")


def test_practice_line_names_the_recording_without_inventing_audio():
    line = explain_practice("TELUGU", "Wind down")
    assert "Wind down" in line
    assert _has_script(line, _SCRIPTS["TELUGU"])

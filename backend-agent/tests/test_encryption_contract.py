"""Canonical encryption contract: docs/ENCRYPTION_DATA_MATRIX.md matches code."""

from __future__ import annotations

from pathlib import Path

import pytest
from mongomock_motor import AsyncMongoMockClient

from services.erasure import _OWNED
from backend_core.security import decrypt_payload, encrypt_payload, open_text, seal_text

def _matrix_text() -> str:
    here = Path(__file__).resolve()
    outer = here.parents[2]
    monolith = outer / "docs" / "ENCRYPTION_DATA_MATRIX.md"
    if (outer / "docker-compose.staging.yml").is_file() and monolith.is_file():
        return monolith.read_text(encoding="utf-8")
    return (here.parents[1] / "docs" / "ENCRYPTION_DATA_MATRIX.md").read_text(encoding="utf-8")


MATRIX = _matrix_text()

SEALED = (
    "messages.content",
    "journal_entries.content",
    "mood_logs.note",
    "student_memories.fact",
    "session_reports.summary",
    "session_reports.psychiatric_summary",
    "user_insights.insight_summary",
    "graph_nodes.name",
    "proactive_questions.question",
    "apm_nodes.display_label",
)

PLAINTEXT_NARRATIVE = (
    "journal_entries.title",
    "mood_logs.mood",
    "session_reports.events",
    "daily_tasks.tasks",
    "habit_events.title",
    "user_patterns.description",
    "graph_relationships.properties",
    "student_psychological_profiles.conversations",
)

SEALED_COLLECTIONS = {item.split(".", 1)[0] for item in SEALED}


def _section(name: str) -> list[str]:
    start = MATRIX.index(f"{name}:")
    chunk = MATRIX[start:].split("```")[0]
    lines: list[str] = []
    for raw in chunk.splitlines()[1:]:
        text = raw.strip()
        if not text:
            if lines:
                break
            continue
        if text.endswith(":") and text[:-1].replace("_", "").isalpha() and text[:-1].isupper():
            break
        lines.append(text)
    return lines


def test_matrix_lists_sealed_and_plaintext_narrative_fields():
    assert _section("SEALED") == list(SEALED)
    assert _section("PLAINTEXT_NARRATIVE") == list(PLAINTEXT_NARRATIVE)
    assert "This is not end-to-end encryption." in MATRIX
    assert "CryptoIntegrityError" in MATRIX
    assert "not written" in MATRIX
    assert "student_psychological_profiles.conversations" in MATRIX


def test_sealed_collections_are_erased_by_user_id_without_decrypt():
    owned = {name for name, _field in _OWNED}
    assert SEALED_COLLECTIONS <= owned


@pytest.mark.asyncio
async def test_every_sealed_writer_stores_ciphertext():
    from journaling.service import create_journal_entry
    from services.chat_history import persist_message
    from services.extraction import SessionExtraction, _persist_extraction
    from services.mongo_graph import generate_node_id, upsert_node
    from student_memory.store import upsert_facts
    from tests.test_encryption_fail_closed import _Store, SECRET_BODY
    from tests.test_journal import _db as journal_db
    from tests.test_journal import _user
    from tests.test_tracking import _db as mood_db
    from tracking.mood import log_mood

    messages = _Store()

    class MsgDB(dict):
        def __getitem__(self, name):
            return messages

    doc, inserted = await persist_message(
        MsgDB(),
        user_id="user_a",
        session_id="s1",
        role="user",
        content=SECRET_BODY,
        idempotency_key="contract-msg",
        seq=1,
    )
    assert inserted is True
    assert doc["content"].startswith("enc::")
    assert SECRET_BODY not in doc["content"]
    assert decrypt_payload(doc["content"]) == SECRET_BODY

    db = journal_db()
    _user(db)
    await create_journal_entry(
        db,
        "user_a",
        title="A real title",
        content="Today was overwhelming and loud.",
        mood="😐",
    )
    journal = db["journal_entries"].docs[0]
    assert journal["content"].startswith("enc::")
    assert "overwhelming" not in journal["content"]
    assert journal["title"] == "A real title"
    assert not str(journal["title"]).startswith("enc::")

    moods = mood_db()
    await log_mood(moods, "user_a", mood="low", note=SECRET_BODY)
    row = moods["mood_logs"].docs[0]
    assert row["note"].startswith("enc::")
    assert SECRET_BODY not in row["note"]
    assert row["mood"] == "low"
    assert not str(row["mood"]).startswith("enc::")

    from unittest.mock import AsyncMock, MagicMock

    memory = _Store()
    memory.find_one = AsyncMock(return_value=None)

    class MemDB(dict):
        def __getitem__(self, name):
            if name == "users":
                users = MagicMock()
                users.find_one = AsyncMock(return_value={"user_id": "user_a"})
                return users
            return memory
    await upsert_facts(
        MemDB(),
        "user_a",
        "s1",
        [{"fact": SECRET_BODY, "category": "OTHER", "importance": 0.5}],
    )
    assert memory.docs[0]["fact"].startswith("enc::")
    assert SECRET_BODY not in memory.docs[0]["fact"]

    insights = _Store()

    class InsightDB(dict):
        def __getitem__(self, name):
            return insights

    await _persist_extraction(
        InsightDB(),
        "user_a",
        "s1",
        SessionExtraction(insight_summary=SECRET_BODY),
    )
    assert insights.docs[0]["insight_summary"].startswith("enc::")
    assert SECRET_BODY not in insights.docs[0]["insight_summary"]

    nodes = _Store()

    class GraphDB(dict):
        def __getitem__(self, name):
            return nodes

    node_id = generate_node_id("user_a", "Emotion", "worry")
    await upsert_node(GraphDB(), "user_a", node_id, "Emotion", SECRET_BODY)
    assert nodes.docs[0]["name"].startswith("enc::")
    assert SECRET_BODY not in nodes.docs[0]["name"]

    from services.proactive.store import ensure_proactive_indexes, insert_opportunity

    proactive_db = AsyncMongoMockClient()["proactive_enc"]
    await ensure_proactive_indexes(proactive_db)
    stored_q = await insert_opportunity(
        proactive_db,
        user_id="user_a",
        event_id="pq_test__nonce1",
        execution_nonce="nonce1",
        trigger_type="FOLLOW_UP_ON_PREVIOUS_CONTEXT",
        question=SECRET_BODY,
        receptivity_state="RECEPTIVE",
        confidence=0.5,
        risk_state="none",
        language="ENGLISH",
        script="LATIN",
        source_node_ids=[],
        status="APPROVED",
    )
    saved = stored_q["doc"]
    assert saved["question"].startswith("enc::")
    assert SECRET_BODY not in saved["question"]

    from schemas import APMExtraction, APMNodeType, APMObservation
    from services.apm import ensure_apm_indexes, persist_apm_extraction

    apm_db = AsyncMongoMockClient()["apm_enc"]
    await ensure_apm_indexes(apm_db)
    await apm_db["users"].insert_one(
        {
            "user_id": "user_a",
            "isActive": True,
            "personalization_consent": True,
        }
    )
    await persist_apm_extraction(
        apm_db,
        "user_a",
        "sess_enc",
        APMExtraction(
            observations=[
                APMObservation(
                    node_type=APMNodeType.TRIGGER,
                    label=SECRET_BODY[:80] if len(SECRET_BODY) > 80 else SECRET_BODY,
                    valence=-0.4,
                    intensity=0.6,
                    evidence_kind="explicit",
                    confidence_score=0.8,
                )
            ]
        ),
        message="presentations feel heavy",
    )
    apm_node = await apm_db["apm_nodes"].find_one({"user_id": "user_a"})
    assert apm_node["display_label"].startswith("enc::")
    assert SECRET_BODY[:20] not in apm_node["display_label"]
    assert apm_node["canonical_label"]

    from services.session_report import generate_session_report
    from tests.test_tasks import _db as tasks_db
    from tests.test_tasks import _user as tasks_user
    from unittest.mock import AsyncMock, MagicMock, patch

    report_db = tasks_db()
    tasks_user(report_db)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"summary": "' + SECRET_BODY + '",'
                '"psychiatric_summary": "' + SECRET_BODY + '",'
                '"valence": 0, "arousal": 0, "dominance": 0, "confidence": 0.5,'
                '"latent_states": [], "crisis_signal": false}'
            )
        )
    )

    async def _load(*_args, **_kwargs):
        return [
            {
                "role": "user",
                "content": "I have a long enough conversation here.",
                "message_kind": "chat",
            }
        ]

    with patch("services.session_report.load_session_messages", _load), patch(
        "llm_provider.get_llm", return_value=llm
    ), patch(
        "services.patterns.retrieve.retrieve_relevant_patterns",
        AsyncMock(return_value=[]),
    ):
        await generate_session_report(report_db, user_id="user_a", session_id="sess")
    stored = report_db["session_reports"].docs[0]
    assert stored["summary"].startswith("enc::")
    assert stored["psychiatric_summary"].startswith("enc::")
    assert SECRET_BODY not in stored["summary"]
    assert SECRET_BODY not in stored["psychiatric_summary"]
    assert stored.get("events") == [] or not str(stored.get("events")).startswith("enc::")


@pytest.mark.asyncio
async def test_documented_plaintext_fields_are_not_sealed():
    from sleep.writer import create_sleep_log
    from tests.test_sleep import _db as sleep_db
    from tests.test_tracking import _db as mood_db
    from tracking.habits import create_habit

    sleep = sleep_db()
    sleep["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    night = await create_sleep_log(
        sleep,
        "user_a",
        bedtime="11:00 PM",
        wake_up_time="7:00 AM",
        date="2026-09-22",
    )
    assert night["bedtime"] == "11:00 PM"
    assert not str(night["bedtime"]).startswith("enc::")

    habits = mood_db()
    saved = await create_habit(habits, "user_a", title="Morning walk")
    assert saved["title"] == "Morning walk"
    stored = habits["habit_events"].docs[0]["title"]
    assert stored == "Morning walk"
    assert not stored.startswith("enc::")


def test_round_trip_and_legacy_passthrough_remain():
    sealed = seal_text("journal body")
    assert sealed.startswith("enc::")
    assert open_text(sealed) == "journal body"
    assert decrypt_payload(encrypt_payload("mood note")) == "mood note"
    assert decrypt_payload("legacy plaintext") == "legacy plaintext"

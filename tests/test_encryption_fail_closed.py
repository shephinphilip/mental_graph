"""Fail-closed encryption: never persist plaintext, never return ciphertext."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.security import (
    CryptoIntegrityError,
    decrypt_payload,
    encrypt_payload,
)


SECRET_BODY = "protected-field-plaintext"


class _Store:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return MagicMock()

    async def update_one(self, query, update, upsert=False):
        self.docs.append(dict(update.get("$set") or {}))
        return MagicMock()

    async def find_one(self, query=None, sort=None, projection=None):
        return None

    def find(self, *args, **kwargs):
        class _Cursor:
            def sort(self, *a, **k):
                return self

            def limit(self, n):
                return self

            async def to_list(self, length=None):
                return []

        return _Cursor()


def test_encrypt_success_never_contains_plaintext():
    sealed = encrypt_payload(SECRET_BODY)
    assert sealed.startswith("enc::")
    assert SECRET_BODY not in sealed
    assert decrypt_payload(sealed) == SECRET_BODY


def test_encrypt_failure_raises_and_does_not_return_plaintext(monkeypatch, caplog):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(Fernet, "encrypt", lambda self, data: (_ for _ in ()).throw(RuntimeError("kdf")))
    with caplog.at_level("ERROR"):
        with pytest.raises(CryptoIntegrityError, match="Encryption failed"):
            encrypt_payload(SECRET_BODY)
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert SECRET_BODY not in joined
    assert "enc::" not in joined
    assert "type=RuntimeError" in joined


def test_wrong_key_never_returns_ciphertext(monkeypatch, caplog):
    sealed = encrypt_payload(SECRET_BODY)
    monkeypatch.setattr(
        "services.security.get_settings",
        lambda: SimpleNamespace(
            ENCRYPTION_SECRET_KEY="a-different-encryption-secret-value",
            APP_ENV="development",
        ),
    )
    with caplog.at_level("ERROR"):
        with pytest.raises(CryptoIntegrityError, match="Decryption failed"):
            decrypt_payload(sealed)
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert SECRET_BODY not in joined
    assert sealed not in joined
    assert "enc::" not in joined
    assert "type=InvalidToken" in joined


@pytest.mark.asyncio
async def test_message_writer_stores_ciphertext_not_plaintext():
    from services.chat_history import persist_message

    messages = _Store()

    class DB(dict):
        def __getitem__(self, name):
            return messages

    db = DB()
    doc, inserted = await persist_message(
        db,
        user_id="user_a",
        session_id="s1",
        role="user",
        content=SECRET_BODY,
        idempotency_key="k1",
        seq=1,
    )
    assert inserted is True
    assert doc["content"].startswith("enc::")
    assert SECRET_BODY not in doc["content"]
    assert messages.docs[0]["content"] == doc["content"]


@pytest.mark.asyncio
async def test_protected_writers_abort_when_encrypt_fails():
    from journaling.service import create_journal_entry
    from services.chat_history import persist_message
    from services.extraction import SessionExtraction, _persist_extraction
    from services.mongo_graph import generate_node_id, upsert_node
    from student_memory.store import upsert_facts
    from tracking.mood import log_mood

    boom = CryptoIntegrityError("Encryption failed")

    messages = _Store()

    class MsgDB(dict):
        def __getitem__(self, name):
            return messages

    with patch("services.chat_history.encrypt_payload", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await persist_message(
                MsgDB(),
                user_id="user_a",
                session_id="s1",
                role="user",
                content=SECRET_BODY,
                idempotency_key="k-fail",
                seq=1,
            )
    assert messages.docs == []

    class CollDB(dict):
        def __init__(self):
            self.store = _Store()
            dict.__init__(self)

        def __getitem__(self, name):
            if name == "users":
                users = MagicMock()
                users.find_one = AsyncMock(
                    return_value={"user_id": "user_a", "email": "a@school.edu"}
                )
                return users
            return self.store

    journal_db = CollDB()
    with patch("journaling.service.seal_text", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await create_journal_entry(
                journal_db,
                "user_a",
                title="A real title",
                content="Today was overwhelming and loud.",
                mood="😐",
            )
    assert journal_db.store.docs == []

    mood_db = CollDB()
    with patch("tracking.mood.seal_text", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await log_mood(mood_db, "user_a", mood="low", note=SECRET_BODY)
    assert mood_db.store.docs == []

    memory_db = CollDB()
    memory_db.store.find_one = AsyncMock(return_value=None)
    with patch("student_memory.store.seal_text", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await upsert_facts(
                memory_db,
                "user_a",
                "s1",
                [{"fact": SECRET_BODY, "category": "OTHER", "importance": 0.5}],
            )
    assert memory_db.store.docs == []

    insights = _Store()

    class InsightDB(dict):
        def __getitem__(self, name):
            return insights

    extraction = SessionExtraction(insight_summary=SECRET_BODY)
    with patch("services.security.seal_text", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await _persist_extraction(InsightDB(), "user_a", "s1", extraction)
    assert insights.docs == []

    nodes = _Store()

    class GraphDB(dict):
        def __getitem__(self, name):
            return nodes

    node_id = generate_node_id("user_a", "Emotion", "worry")
    with patch("services.mongo_graph.seal_text", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await upsert_node(GraphDB(), "user_a", node_id, "Emotion", SECRET_BODY)
    assert nodes.docs == []

    from services.chat_history import update_welcome_message

    welcome = _Store()

    class WelcomeDB(dict):
        def __getitem__(self, name):
            return welcome

    with patch("services.chat_history.encrypt_payload", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await update_welcome_message(
                WelcomeDB(),
                user_id="user_a",
                session_id="s1",
                content=SECRET_BODY,
                response_language="ENGLISH",
                response_script="LATIN",
            )
    assert welcome.docs == []

    from services.session_report import generate_session_report
    from tests.test_tasks import _db as tasks_db
    from tests.test_tasks import _user as tasks_user

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
    ), patch("services.security.seal_text", side_effect=boom):
        with pytest.raises(CryptoIntegrityError):
            await generate_session_report(report_db, user_id="user_a", session_id="sess")
    assert report_db["session_reports"].docs == []


def test_wrong_key_resume_response_is_structured_and_has_no_ciphertext():
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from app import app
    from database import get_db
    from services.users import issue_access_token

    sealed = encrypt_payload(SECRET_BODY)

    class _Msgs:
        async def find_one(self, query=None, sort=None, projection=None):
            return {
                "session_id": "s1",
                "user_id": "user_a",
                "role": "user",
                "content": sealed,
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                "seq": 1,
                "message_kind": "chat",
                "message_id": "m1",
            }

        def find(self, *args, **kwargs):
            doc = {
                "session_id": "s1",
                "user_id": "user_a",
                "role": "user",
                "content": sealed,
                "created_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ),
                "seq": 1,
                "message_kind": "chat",
                "message_id": "m1",
            }

            class _C:
                def sort(self, *a, **k):
                    return self

                def limit(self, n):
                    return self

                async def to_list(self, length=None):
                    return [doc]

            return _C()

    class _Users:
        async def find_one(self, *args, **kwargs):
            return {"user_id": "user_a"}

    class _DB(dict):
        def __getitem__(self, name):
            if name == "messages":
                return _Msgs()
            return _Users()

    fake = _DB()
    app.dependency_overrides[get_db] = lambda: fake
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"ok": 1})
    with patch("database.create_mongo_client", return_value=mongo), patch(
        "database.ensure_all_indexes", new_callable=AsyncMock
    ), patch(
        "services.security.get_settings",
        lambda: SimpleNamespace(
            ENCRYPTION_SECRET_KEY="a-different-encryption-secret-value",
            APP_ENV="development",
            ENFORCE_PII_ANONYMIZATION=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            "/chat/session/user_a/resume",
            headers={"Authorization": "Bearer " + issue_access_token("user_a")},
            params={"session_id": "s1"},
        )
    app.dependency_overrides.clear()
    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert body["detail"] == "An unexpected error occurred."
    assert SECRET_BODY not in response.text
    assert "enc::" not in response.text
    assert sealed not in response.text


@pytest.mark.asyncio
async def test_journal_and_mood_success_store_ciphertext_not_plaintext():
    from journaling.service import create_journal_entry
    from tests.test_journal import _db as journal_db
    from tests.test_journal import _user
    from tests.test_tracking import _db as mood_db
    from tracking.mood import log_mood

    db = journal_db()
    _user(db)
    await create_journal_entry(
        db,
        "user_a",
        title="A real title",
        content="Today was overwhelming and loud.",
        mood="😐",
    )
    stored = db["journal_entries"].docs[0]["content"]
    assert stored.startswith("enc::")
    assert "overwhelming" not in stored

    moods = mood_db()
    await log_mood(moods, "user_a", mood="low", note=SECRET_BODY)
    note = moods["mood_logs"].docs[0]["note"]
    assert note.startswith("enc::")
    assert SECRET_BODY not in note


def test_cross_user_chat_authorization_unchanged():
    from tests.api.test_http_contract import test_cross_user_chat_is_403

    test_cross_user_chat_is_403()


@pytest.mark.asyncio
async def test_cross_user_journal_and_mood_authorization_unchanged():
    from journaling.service import create_journal_entry
    from tests.test_journal import _db as journal_db
    from tests.test_journal import _user
    from tests.test_tracking import _db as mood_db
    from tracking.mood import log_mood

    db = journal_db()
    _user(db, "user_a", "a@school.edu")
    _user(db, "user_b", "b@school.edu")
    with pytest.raises(PermissionError):
        await create_journal_entry(
            db,
            "user_a",
            title="A real title",
            content="Today was overwhelming and loud.",
            mood="😢",
            claimed_user_id="user_b",
        )
    assert db["journal_entries"].docs == []

    moods = mood_db()
    with pytest.raises(PermissionError):
        await log_mood(moods, "user_a", mood="okay", claimed_user_id="user_b")
    assert moods["mood_logs"].docs == []

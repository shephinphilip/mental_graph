"""Report tasks append to daily_tasks and stay isolated to the signed-in user."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.apm import record_intervention_feedback
from tasks.context import build_task_context
from tasks.store import (
    add_custom_task,
    complete_task,
    list_today,
    patch_custom_task,
    persist_report_tasks,
)
from tasks.validate import validate_proposals


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs if length is None else self._docs[:length])


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [doc for doc in self.docs if _match(doc, query or {})]
        return found[0] if found else None

    async def update_one(self, query, update, upsert=False):
        matched = [doc for doc in self.docs if _match(doc, query)]
        if matched:
            doc = matched[0]
            if "$set" in update:
                doc.update(update["$set"])
            if "$push" in update:
                for field, spec in update["$push"].items():
                    extra = spec.get("$each") if isinstance(spec, dict) else [spec]
                    doc.setdefault(field, []).extend(extra)
            return SimpleNamespace(modified_count=1)
        if upsert:
            created = dict(update.get("$setOnInsert") or {})
            self.docs.append(created)
            return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def insert_one(self, doc):
        self.docs.append(doc)
        return SimpleNamespace()


def _elem(row, spec):
    for key, value in spec.items():
        if isinstance(value, dict) and "$ne" in value:
            if row.get(key) == value["$ne"]:
                return False
        elif row.get(key) != value:
            return False
    return True


def _match(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_match(doc, clause) for clause in value):
                return False
            continue
        if isinstance(value, dict):
            if "$in" in value and doc.get(key) not in value["$in"]:
                return False
            if "$elemMatch" in value:
                rows = doc.get(key) or []
                if not any(_elem(row, value["$elemMatch"]) for row in rows if isinstance(row, dict)):
                    return False
            if "$not" in value and "$elemMatch" in value["$not"]:
                rows = doc.get(key) or []
                if any(_elem(row, value["$not"]["$elemMatch"]) for row in rows if isinstance(row, dict)):
                    return False
        elif doc.get(key) != value:
            return False
    return True


def _db():
    collections: Dict[str, _Collection] = {}

    class DB(dict):
        def __getitem__(self, name):
            collections.setdefault(name, _Collection())
            return collections[name]

    db = DB()
    db._c = collections
    return db


def _user(db, user_id="user_a", email="a@school.edu"):
    db["users"].docs.append({"user_id": user_id, "email": email})


def _seed_day(db):
    db["daily_tasks"].docs.append(
        {
            "_id": "day",
            "user_id": "user_a",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "created_at": datetime.now(timezone.utc),
            "tasks": [
                {
                    "id": "task_old1234",
                    "title": "Complete physics worksheet",
                    "description": "Already done",
                    "completed": True,
                    "is_custom": False,
                    "is_deleted": False,
                },
                {
                    "id": "custom_aabbccdd",
                    "title": "Call mother",
                    "description": "",
                    "completed": False,
                    "is_custom": True,
                    "is_deleted": False,
                    "source": "CUSTOM",
                },
                {
                    "id": "task_gone",
                    "title": "Old drill",
                    "description": "",
                    "completed": False,
                    "is_custom": False,
                    "is_deleted": True,
                },
            ],
        }
    )


def test_validation_trims_and_drops_bad_rows():
    raw = [
        {"title": "Study harder", "description": "no"},
        {"title": "", "description": "missing"},
        {"title": "x" * 90, "description": "too long"},
        {"title": "Review two questions", "description": "y" * 300},
        {"title": "Review two questions", "description": "Twenty minutes on the ones you missed."},
        {"title": "Write the worry down", "description": "Three lines before bed."},
        {"title": "Walk to the window", "description": "Two minutes, then sit."},
        {"title": "Fourth extra", "description": "Should not be kept."},
        "nope",
    ]
    kept = validate_proposals(raw, [])
    assert [item["title"] for item in kept] == [
        "Review two questions",
        "Write the worry down",
        "Walk to the window",
    ]
    assert validate_proposals([], [{"title": "Review two questions"}]) == []


@pytest.mark.asyncio
async def test_report_tasks_append_once_and_keep_existing_rows():
    db = _db()
    _user(db)
    _seed_day(db)
    skipped = await persist_report_tasks(db, "user_a", "sess", [])
    assert skipped["task_persistence"] == "skipped"
    assert len(db["daily_tasks"].docs[0]["tasks"]) == 3

    first = await persist_report_tasks(
        db,
        "user_a",
        "sess",
        [
            {"title": "Complete physics worksheet", "description": "duplicate"},
            {"title": "Review the first three physics questions", "description": "Twenty minutes."},
        ],
    )
    assert first["task_persistence"] == "saved"
    assert [task["title"] for task in first["tasks"]] == ["Review the first three physics questions"]
    stored = db["daily_tasks"].docs[0]["tasks"]
    assert stored[0]["completed"] is True
    assert stored[1]["is_custom"] is True and stored[1]["title"] == "Call mother"
    assert stored[2]["is_deleted"] is True
    assert stored[3]["source"] == "REPORT"
    assert stored[3]["source_session_id"] == "sess"
    assert stored[3]["id"].startswith("task_")

    again = await persist_report_tasks(
        db,
        "user_a",
        "sess",
        [{"title": "A different task", "description": "Should not be added."}],
    )
    assert again["task_persistence"] == "existing"
    assert len(db["daily_tasks"].docs[0]["tasks"]) == 4


@pytest.mark.asyncio
async def test_cross_user_and_completion_and_context():
    db = _db()
    _user(db)
    _user(db, "user_b", "b@school.edu")
    saved = await persist_report_tasks(
        db,
        "user_a",
        "sess",
        [{"title": "Start the first three questions", "description": "Twenty minutes."}],
    )
    with pytest.raises(PermissionError):
        await list_today(db, "user_a", claimed_user_id="user_b")
    with pytest.raises(PermissionError):
        await add_custom_task(db, "user_a", title="Not yours", claimed_user_id="user_b")
    with pytest.raises(LookupError):
        await complete_task(db, "user_b", saved["tasks"][0]["id"])
    done = await complete_task(db, "user_a", saved["tasks"][0]["id"])
    assert done["completed"] is True
    listed = await list_today(db, "user_a", claimed_user_id="user_a")
    assert listed["tasks"][0]["completed"] is True
    context = await build_task_context(db, "user_a")
    assert "Start the first three questions" in context
    assert "completed" in context
    assert "source_session_id" not in context
    other = await build_task_context(db, "user_b")
    assert "Start the first three questions" not in other


@pytest.mark.asyncio
async def test_custom_patch_soft_deletes_without_touching_report_tasks():
    db = _db()
    _user(db)
    custom = await add_custom_task(db, "user_a", title="Call mother", description="Tonight")
    await persist_report_tasks(
        db, "user_a", "sess", [{"title": "Review question 2", "description": "Ten minutes."}]
    )
    hidden = await patch_custom_task(db, "user_a", custom["id"], is_deleted=True, claimed_user_id="user_a")
    assert hidden["id"] == custom["id"]
    visible = await list_today(db, "user_a")
    assert [task["title"] for task in visible["tasks"]] == ["Review question 2"]
    raw = db["daily_tasks"].docs[0]["tasks"]
    assert any(task["id"] == custom["id"] and task["is_deleted"] for task in raw)


@pytest.mark.asyncio
async def test_crisis_and_write_failure_do_not_drop_the_report_or_corrupt_tasks():
    db = _db()
    _user(db)
    _seed_day(db)
    crisis = await persist_report_tasks(
        db,
        "user_a",
        "sess",
        [{"title": "Breathe for five minutes", "description": "Ordinary task."}],
        crisis=True,
    )
    assert crisis["tasks"] == []
    assert len(db["daily_tasks"].docs[0]["tasks"]) == 3

    with patch.object(_Collection, "update_one", side_effect=RuntimeError("disk")):
        failed = await persist_report_tasks(
            db,
            "user_a",
            "sess",
            [{"title": "Review question 2", "description": "Ten minutes."}],
        )
    assert failed["task_persistence"] == "failed"
    assert failed["tasks"][0]["title"] == "Review question 2"
    assert len(db["daily_tasks"].docs[0]["tasks"]) == 3
    assert record_intervention_feedback  # creation is not wired to this helper


@pytest.mark.asyncio
async def test_failed_report_does_not_create_tasks_and_ui_reads_the_api():
    from services.session_report import generate_session_report

    db = _db()
    _user(db)

    async def _empty(*_args, **_kwargs):
        return []

    with patch("services.session_report.load_session_messages", _empty):
        with pytest.raises(ValueError):
            await generate_session_report(db, user_id="user_a", session_id="sess")
    assert db["daily_tasks"].docs == []

    app = Path(__file__).resolve().parents[1]
    streamlit = (app / "streamlit_app.py").read_text(encoding="utf-8")
    graph = (app / "services" / "graph.py").read_text(encoding="utf-8")
    assert "/api/report_card/tasks/" in streamlit
    assert "Suggested for today" in streamlit
    assert "daily_tasks" not in streamlit
    assert "persist_report_tasks" not in graph
    assert 'task_context=context.get("task_context")' in graph


@pytest.mark.asyncio
async def test_session_report_persists_model_tasks(monkeypatch):
    from services.session_report import generate_session_report

    db = _db()
    _user(db)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"summary": "The worksheet was still waiting.",'
                '"valence": -0.2, "arousal": 0.2, "dominance": 0.0, "confidence": 0.8,'
                '"latent_states": [{"state": "ANXIETY_HIGH", "probability": 0.6}],'
                '"crisis_signal": false,'
                '"tasks": [{"title": "Start the first three questions", "description": "Twenty minutes."}]}'
            )
        )
    )

    async def _load(*_args, **_kwargs):
        return [{"role": "user", "content": "I have not started the physics worksheet.", "message_kind": "chat"}]

    with patch("services.session_report.load_session_messages", _load), patch(
        "llm_provider.get_llm", return_value=llm
    ), patch(
        "services.patterns.retrieve.retrieve_relevant_patterns", AsyncMock(return_value=[])
    ):
        report = await generate_session_report(db, user_id="user_a", session_id="sess")
        second = await generate_session_report(db, user_id="user_a", session_id="sess")

    assert report["tasks"][0]["title"] == "Start the first three questions"
    assert report["task_persistence"] == "saved"
    assert "source_session_id" not in report["tasks"][0]
    assert second["task_persistence"] == "existing"
    assert len(db["daily_tasks"].docs[0]["tasks"]) == 1
    monkeypatch.setattr(
        "services.apm.record_intervention_feedback",
        AsyncMock(side_effect=AssertionError("task write must not be an APM success")),
    )
    await complete_task(db, "user_a", report["tasks"][0]["id"])

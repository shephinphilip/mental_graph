"""Session readings feed the welcome. Transcripts do not."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from prompts import SESSION_PHASE_OPENING, WELCOME_USER_CUE, format_system_prompt
from reports.context import format_prior_reports
from reports.models import clamp_metric, merge_events, normalize_events
from reports.resolve import is_affirmation, maybe_resolve_events
from reports.store import accept_proposed_task, save_reading


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
        if sort:
            key, direction = sort[0]
            found.sort(key=lambda doc: doc.get(key) or "", reverse=direction < 0)
        return found[0] if found else None

    async def update_one(self, query, update, upsert=False):
        matched = [doc for doc in self.docs if _match(doc, query)]
        if matched:
            if "$set" in update:
                matched[0].update(update["$set"])
            if "$push" in update:
                for field, spec in update["$push"].items():
                    extra = spec.get("$each") if isinstance(spec, dict) else [spec]
                    matched[0].setdefault(field, []).extend(extra)
            return SimpleNamespace(modified_count=1)
        if upsert:
            created = {}
            for key, value in (query or {}).items():
                if not isinstance(value, dict):
                    created[key] = value
            created.update(update.get("$setOnInsert") or {})
            created.update(update.get("$set") or {})
            self.docs.append(created)
        return SimpleNamespace(modified_count=0)


def _match(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_match(doc, clause) for clause in value):
                return False
            continue
        if isinstance(value, dict) and "$in" in value:
            if doc.get(key) not in value["$in"]:
                return False
            continue
        if doc.get(key) != value:
            return False
    return True


def _db():
    collections: Dict[str, _Collection] = {}

    class DB(dict):
        def __getitem__(self, name):
            collections.setdefault(name, _Collection())
            return collections[name]

    db = DB()
    db["users"].docs.append({"user_id": "user_a", "email": "a@school.edu"})
    db["users"].docs.append({"user_id": "user_b", "email": "b@school.edu"})
    return db


def test_metric_clamps_and_events_start_from_the_model_flag():
    assert clamp_metric(0.1) == 1
    assert clamp_metric(12) == 10
    assert clamp_metric("nope") is None
    events = normalize_events(
        [{"label": "Physics mock", "resolved": False}, {"label": "Physics mock"}]
    )
    assert len(events) == 1
    assert events[0]["resolved"] is False


def test_settled_events_stay_settled_when_a_reading_is_repeated():
    previous = [{"event_id": "evt_old", "label": "Physics mock", "resolved": True}]
    fresh = normalize_events([{"label": "Physics mock", "resolved": False}])
    merged = merge_events(fresh, previous)
    assert merged[0]["resolved"] is True
    assert merged[0]["event_id"] == "evt_old"


def test_welcome_context_uses_open_events_and_hides_settled_ones():
    text = format_prior_reports(
        [
            {
                "psychiatric_summary": "The sitting was about a mock that still felt unfinished.",
                "psychiatric_metric": 6,
                "events": [
                    {"label": "Physics mock", "resolved": False},
                    {"label": "Argument with dad", "resolved": True},
                ],
            }
        ]
    )
    assert "Physics mock" in text
    assert "Argument with dad" not in text
    assert "Do not quote" in text
    assert "Student:" not in text
    prompt = format_system_prompt(last_session_context=text)
    assert "Physics mock" in prompt
    assert "open event" in SESSION_PHASE_OPENING.lower()
    assert "Do not retell" in WELCOME_USER_CUE


def test_empty_reports_do_not_invent_a_transcript():
    text = format_prior_reports([])
    assert "first conversation" in text
    assert "message" not in text.lower()


@pytest.mark.asyncio
async def test_reading_is_stored_and_only_a_chosen_task_is_added():
    db = _db()
    reading = await save_reading(
        db,
        user_id="user_a",
        session_id="sess",
        summary="The worksheet was still waiting.",
        parsed={
            "psychiatric_summary": "A worksheet was still sitting there.",
            "psychiatric_metric": 6.4,
            "events": [{"label": "Physics worksheet", "resolved": False}],
            "tasks": [
                {"title": "Start the first three questions", "description": "Twenty minutes."},
                {"title": "Pack the bag tonight", "description": "Before sleep."},
            ],
        },
        crisis=False,
    )
    assert reading["psychiatric_metric"] == 6
    assert reading["task_persistence"] == "proposed"
    assert db["daily_tasks"].docs == []
    db["session_reports"].docs.append(
        {
            "user_id": "user_a",
            "session_id": "sess",
            "proposed_tasks": reading["proposed_tasks"],
            "events": reading["events"],
            "created_at": "2026-09-25T00:00:00",
        }
    )
    first = reading["proposed_tasks"][0]
    accepted = await accept_proposed_task(db, "user_a", "sess", first["id"])
    assert accepted["task_persistence"] == "saved"
    titles = [task["title"] for task in db["daily_tasks"].docs[0]["tasks"]]
    assert titles == ["Start the first three questions"]
    again = await accept_proposed_task(db, "user_a", "sess", first["id"])
    assert again["task_persistence"] == "existing"
    assert len(db["daily_tasks"].docs[0]["tasks"]) == 1


@pytest.mark.asyncio
async def test_another_user_cannot_accept_the_task():
    db = _db()
    db["session_reports"].docs.append(
        {
            "user_id": "user_a",
            "session_id": "sess",
            "proposed_tasks": [
                {
                    "id": "proposal_abc",
                    "title": "Start the first three questions",
                    "description": "Twenty minutes.",
                    "added": False,
                }
            ],
            "created_at": "2026-09-25T00:00:00",
        }
    )
    with pytest.raises(LookupError):
        await accept_proposed_task(db, "user_b", "sess", "proposal_abc")


@pytest.mark.asyncio
async def test_yes_settles_only_the_event_just_named():
    db = _db()
    db["session_reports"].docs.append(
        {
            "user_id": "user_a",
            "session_id": "old",
            "created_at": "2026-09-25T00:00:00",
            "events": [
                {"event_id": "evt_physics", "label": "Physics mock", "resolved": False},
                {"event_id": "evt_home", "label": "Home argument", "resolved": False},
            ],
        }
    )
    assert is_affirmation("Yes")
    none = await maybe_resolve_events(
        db,
        "user_a",
        "Yes",
        "Want to start with whatever is on your mind?",
    )
    assert none == []
    settled = await maybe_resolve_events(
        db,
        "user_a",
        "Yes",
        "Last time a physics mock was still open. Is that settled?",
    )
    assert settled == ["evt_physics"]
    stored = db["session_reports"].docs[0]["events"]
    by_id = {event["event_id"]: event["resolved"] for event in stored}
    assert by_id["evt_physics"] is True
    assert by_id["evt_home"] is False
    welcome = format_prior_reports(db["session_reports"].docs)
    assert "Physics mock" not in welcome
    assert "Home argument" in welcome


@pytest.mark.asyncio
async def test_opening_turn_does_not_treat_the_cue_as_a_yes():
    db = _db()
    db["session_reports"].docs.append(
        {
            "user_id": "user_a",
            "session_id": "old",
            "created_at": "2026-09-25T00:00:00",
            "events": [{"event_id": "evt_physics", "label": "Physics mock", "resolved": False}],
        }
    )
    settled = await maybe_resolve_events(
        db,
        "user_a",
        "Yes",
        "Is the physics mock settled?",
        opening_turn=True,
    )
    assert settled == []
    assert db["session_reports"].docs[0]["events"][0]["resolved"] is False

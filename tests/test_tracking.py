"""Mood check-ins and habits, written in the shapes the AI already reads."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from services.context import _fetch_active_habits, _fetch_recent_moods
from services.patterns.adapters import _mood_observations
from tracking.habits import (
    check_in,
    create_habit,
    current_streak,
    list_habits,
    set_streak_visibility,
    update_habit,
)
from tracking.mood import clamp_score, log_mood, recent_moods, resolve_logged_at


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda doc: doc.get(key) or 0, reverse=direction < 0)
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs if length is None else self._docs[:length])

    def __aiter__(self):
        async def gen():
            for doc in self._docs:
                yield doc

        return gen()


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def find(self, query=None, projection=None):
        return _Cursor([doc for doc in self.docs if _match(doc, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [doc for doc in self.docs if _match(doc, query or {})]
        return found[0] if found else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    async def update_one(self, query, update, upsert=False):
        matched = [doc for doc in self.docs if _match(doc, query)]
        if matched:
            matched[0].update(update.get("$set") or {})
            return SimpleNamespace(modified_count=1)
        if upsert:
            created = {
                key: value
                for key, value in (query or {}).items()
                if not isinstance(value, dict)
            }
            created.update(update.get("$set") or {})
            self.docs.append(created)
        return SimpleNamespace(modified_count=0)


def _match(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_match(doc, clause) for clause in value):
                return False
            continue
        actual = doc.get(key)
        if isinstance(value, dict):
            if "$in" in value and actual not in value["$in"]:
                return False
            if "$ne" in value and actual == value["$ne"]:
                return False
            if "$gte" in value and (actual is None or actual < value["$gte"]):
                return False
            continue
        if actual != value:
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


# ── mood check-ins ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_check_in_reaches_the_prompt_reader_unchanged():
    db = _db()
    await log_mood(db, "user_a", mood="anxious", score=3, note="Could not focus")
    text = await _fetch_recent_moods(db, "user_a", 7)
    assert "anxious" in text
    assert "(3/10)" in text
    assert '"Could not focus"' in text
    assert "No mood logs" not in text


@pytest.mark.asyncio
async def test_a_check_in_is_dated_for_both_readers():
    """The prompt sorts on logged_at; the pattern engine windows on created_at."""
    db = _db()
    await log_mood(db, "user_a", mood="low")
    doc = db["mood_logs"].docs[0]
    assert doc["logged_at"] == doc["created_at"]
    since = datetime.now(timezone.utc) - timedelta(days=7)
    observed = await _mood_observations(db, "user_a", since)
    assert observed and observed[0]["value"] == "low"


@pytest.mark.asyncio
async def test_only_the_mood_is_required():
    db = _db()
    result = await log_mood(db, "user_a", mood="okay")
    assert result["entry"]["score"] is None
    assert result["entry"]["note"] == ""
    with pytest.raises(ValueError):
        await log_mood(db, "user_a", mood="   ")


def test_a_slider_value_lands_between_one_and_ten():
    assert clamp_score(0) == 1
    assert clamp_score(11) == 10
    assert clamp_score(4.6) == 5
    assert clamp_score("nope") is None
    assert clamp_score(None) is None


@pytest.mark.asyncio
async def test_an_offline_retry_does_not_log_twice():
    db = _db()
    first = await log_mood(db, "user_a", mood="flat", client_event_id="evt-1")
    second = await log_mood(db, "user_a", mood="flat", client_event_id="evt-1")
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert len(db["mood_logs"].docs) == 1


def test_a_backfilled_time_is_kept_but_a_future_one_is_not():
    now = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    yesterday = now - timedelta(days=1)
    assert resolve_logged_at(yesterday, now=now) == yesterday
    assert resolve_logged_at(now + timedelta(days=3), now=now) == now
    ancient = now - timedelta(days=400)
    assert resolve_logged_at(ancient, now=now) > ancient


@pytest.mark.asyncio
async def test_a_crisis_note_is_stored_flagged_and_kept_out_of_pattern_learning():
    db = _db()
    result = await log_mood(db, "user_a", mood="low", note="I want to kill myself")
    assert result["crisis"] is True
    assert db["mood_logs"].docs[0]["crisis_flagged"] is True
    since = datetime.now(timezone.utc) - timedelta(days=7)
    assert await _mood_observations(db, "user_a", since) == []


@pytest.mark.asyncio
async def test_one_person_cannot_log_or_read_for_another():
    db = _db()
    with pytest.raises(PermissionError):
        await log_mood(db, "user_a", mood="okay", claimed_user_id="user_b")
    await log_mood(db, "user_b", mood="stressed")
    assert await recent_moods(db, "user_a") == []
    with pytest.raises(PermissionError):
        await recent_moods(db, "user_a", claimed_user_id="user_b")


# ── habits ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_new_habit_reaches_the_prompt_reader():
    db = _db()
    await create_habit(db, "user_a", title="Morning walk", frequency="daily")
    text = await _fetch_active_habits(db, "user_a")
    assert "• Morning walk (daily)" in text


@pytest.mark.asyncio
async def test_a_paused_habit_leaves_the_prompt():
    db = _db()
    habit = await create_habit(db, "user_a", title="Evening journal")
    await update_habit(db, "user_a", habit["habit_id"], status="paused")
    assert await _fetch_active_habits(db, "user_a") == "No active habits tracked."


@pytest.mark.asyncio
async def test_checking_in_twice_on_one_day_changes_nothing():
    db = _db()
    habit = await create_habit(db, "user_a", title="Water")
    first = await check_in(db, "user_a", habit["habit_id"])
    second = await check_in(db, "user_a", habit["habit_id"])
    assert first["already_logged"] is False
    assert second["already_logged"] is True
    assert len(db["habit_events"].docs[0]["completions"]) == 1


def test_one_missed_day_does_not_end_a_streak_but_two_do():
    today = date(2026, 9, 26)
    kept = [(today - timedelta(days=n)).isoformat() for n in range(0, 7)]
    assert current_streak(kept, "daily", today=today) == 7

    with_one_gap = [day for day in kept if day != (today - timedelta(days=2)).isoformat()]
    assert current_streak(with_one_gap, "daily", today=today) == 6

    with_two_gaps = [
        day
        for day in kept
        if day
        not in {
            (today - timedelta(days=1)).isoformat(),
            (today - timedelta(days=2)).isoformat(),
        }
    ]
    assert current_streak(with_two_gaps, "daily", today=today) == 1


def test_an_empty_history_is_simply_zero():
    assert current_streak([], "daily", today=date(2026, 9, 26)) == 0


def test_a_weekly_habit_counts_weeks_and_forgives_an_open_week():
    today = date(2026, 9, 26)
    weeks = [(today - timedelta(days=7 * n)).isoformat() for n in range(1, 4)]
    assert current_streak(weeks, "weekly", today=today) == 3


@pytest.mark.asyncio
async def test_streaks_stay_off_until_the_person_asks_for_them():
    db = _db()
    habit = await create_habit(db, "user_a", title="Stretch")
    checked = await check_in(db, "user_a", habit["habit_id"])
    assert checked["habit"].get("streak_hidden") is True
    assert "streak" not in checked["habit"]
    assert await _fetch_active_habits(db, "user_a") == "• Stretch (daily)"

    await set_streak_visibility(db, "user_a", True)
    listed = await list_habits(db, "user_a")
    assert listed["show_streaks"] is True
    assert listed["habits"][0]["streak"] == 1
    assert "1-day streak" in await _fetch_active_habits(db, "user_a")

    await set_streak_visibility(db, "user_a", False)
    assert "streak" not in await _fetch_active_habits(db, "user_a")


@pytest.mark.asyncio
async def test_one_person_cannot_touch_another_persons_habits():
    db = _db()
    habit = await create_habit(db, "user_b", title="Sleep by 11")
    with pytest.raises(PermissionError):
        await create_habit(db, "user_a", title="Sneak", claimed_user_id="user_b")
    with pytest.raises(LookupError):
        await check_in(db, "user_a", habit["habit_id"])
    assert (await list_habits(db, "user_a"))["habits"] == []


@pytest.mark.asyncio
async def test_a_habit_needs_a_real_name_and_a_known_rhythm():
    db = _db()
    with pytest.raises(ValueError):
        await create_habit(db, "user_a", title="x")
    with pytest.raises(ValueError):
        await create_habit(db, "user_a", title="Read", frequency="hourly")
    with pytest.raises(ValueError):
        await create_habit(db, "user_a", title="Read", reminder_time="9am")
    habit = await create_habit(db, "user_a", title="Read", reminder_time="7:5")
    assert habit["reminder_time"] == "07:05"


def test_every_documented_route_is_actually_mounted():
    """
    The rest of this file calls the service functions directly, so it stayed
    green for a while when none of these routes existed and every client got
    a 404. Assert the mounting itself.
    """
    from app import app

    mounted = {
        (path, method)
        for route in app.routes
        for path in [getattr(route, "path", "")]
        for method in getattr(route, "methods", set()) or set()
    }
    for expected in [
        ("/api/mood", "POST"),
        ("/api/mood/recent", "GET"),
        ("/api/habits", "GET"),
        ("/api/habits", "POST"),
        ("/api/habits/{habit_id}", "PATCH"),
        ("/api/habits/{habit_id}/check-in", "POST"),
        ("/api/habits/streaks", "POST"),
    ]:
        assert expected in mounted, f"{expected[1]} {expected[0]} is not mounted"


def test_the_indexes_are_ensured_at_startup():
    """A partial unique index is what actually makes an offline retry safe."""
    import inspect

    import database

    assert "ensure_tracking_indexes" in inspect.getsource(database.lifespan)

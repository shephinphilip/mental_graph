"""Consultation decisions, cooldown, audit, and access."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from consultation.context import build_care_context
from consultation.evaluate import (
    decide,
    evaluate_user_for_consultation,
    latest_evaluation,
    manual_override,
)
from consultation.roles import target_user
from prompts import format_system_prompt

NOW = datetime(2026, 9, 26, 0, 0, tzinfo=timezone.utc)


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction):
        self._docs.sort(key=lambda d: d.get(key) or NOW, reverse=direction < 0)
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
        return _Cursor([d for d in self.docs if _match(d, query or {})])

    async def find_one(self, query=None, projection=None, sort=None):
        found = [d for d in self.docs if _match(d, query or {})]
        if sort:
            key, direction = sort[0]
            found.sort(key=lambda d: d.get(key) or NOW, reverse=direction < 0)
        return found[0] if found else None

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace()

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _match(doc, query):
                doc.update(update.get("$set") or {})
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)


def _match(doc, query):
    for key, value in query.items():
        if key == "$or":
            if not any(_match(doc, c) for c in value):
                return False
            continue
        if isinstance(value, dict):
            if "$in" in value and doc.get(key) not in value["$in"]:
                return False
            if "$ne" in value and doc.get(key) == value["$ne"]:
                return False
            continue
        if doc.get(key) != value:
            return False
    return True


def _db():
    cols: Dict[str, _Collection] = {}

    class DB(dict):
        def __getitem__(self, name):
            cols.setdefault(name, _Collection())
            return cols[name]

    db = DB()
    db["users"].docs.append({"user_id": "stu_a", "email": "a@s.edu", "roles": ["student"]})
    db["users"].docs.append({"user_id": "stu_b", "email": "b@s.edu", "roles": ["student"]})
    db["users"].docs.append({"user_id": "staff_1", "email": "c@s.edu", "roles": ["counselor"]})
    return db


def _report(user, metric, days_ago, crisis=False):
    return {
        "user_id": user,
        "session_id": f"s{metric}{days_ago}",
        "psychiatric_metric": metric,
        "crisis_signal": crisis,
        "created_at": NOW - timedelta(days=days_ago),
    }


def test_rules_are_conservative():
    assert decide({"elevated_reports": 0, "risk_turns": 0, "risk_average": 0})["status"] == "NOT_NEEDED"
    assert decide({"elevated_reports": 1, "reports_considered": 3})["status"] == "MONITORING"
    assert decide({"elevated_reports": 2, "reports_considered": 3})["status"] == "REFERRED"
    assert decide({"persistent_distress": True})["status"] == "REFERRED"
    assert decide({"crisis_flag_recent": True})["status"] == "REFERRED"
    assert decide({"risk_turns": 3, "risk_average": 6.5})["status"] == "MONITORING"
    assert decide({"risk_turns": 6, "risk_average": 8.2})["status"] == "REFERRED"


@pytest.mark.asyncio
async def test_two_heavy_reports_refer_once_and_then_cool_down():
    db = _db()
    db["session_reports"].docs += [_report("stu_a", 8, 1), _report("stu_a", 7, 3), _report("stu_a", 4, 5)]

    first = await evaluate_user_for_consultation(db, "stu_a", trigger="REPORT", actor="system", now=NOW)
    assert first["status"] == "REFERRED"
    assert first["notification_written"] is True
    assert len(db["consultation_notifications"].docs) == 1
    assert db["consultation_notifications"].docs[0]["read"] is False

    second = await evaluate_user_for_consultation(
        db, "stu_a", trigger="REPORT", actor="system", now=NOW + timedelta(days=10)
    )
    assert second["status"] == "REFERRED"
    assert second["cooldown_active"] is True
    assert second["notification_written"] is False
    assert len(db["consultation_notifications"].docs) == 1

    third = await evaluate_user_for_consultation(
        db, "stu_a", trigger="REPORT", actor="system", now=NOW + timedelta(days=31)
    )
    # Reports have aged out of the lookback window: no new referral, no notification.
    assert third["status"] == "NOT_NEEDED"
    assert len(db["consultation_notifications"].docs) == 1

    actions = [row["action"] for row in db["psychiatric_evaluation_audit"].docs]
    assert actions.count("EVALUATION_RUN") == 3
    assert "NOTIFICATION_WRITTEN" in actions
    assert "REFERRAL_SUPPRESSED_COOLDOWN" in actions
    assert all(row["userId"] == "stu_a" for row in db["psychiatric_evaluations"].docs)


@pytest.mark.asyncio
async def test_one_heavy_report_only_monitors():
    db = _db()
    db["session_reports"].docs += [_report("stu_a", 9, 1), _report("stu_a", 3, 2)]
    result = await evaluate_user_for_consultation(db, "stu_a", now=NOW)
    assert result["status"] == "MONITORING"
    assert db["consultation_notifications"].docs == []


@pytest.mark.asyncio
async def test_persistent_distress_pattern_refers_without_new_scoring():
    db = _db()
    db["user_patterns"].docs.append(
        {
            "user_id": "stu_a",
            "status": "ESTABLISHED_PERSISTENT_DISTRESS",
            "last_observed_at": NOW - timedelta(days=2),
        }
    )
    result = await evaluate_user_for_consultation(db, "stu_a", now=NOW)
    assert result["status"] == "REFERRED"
    assert "persistent distress" in " ".join(result["reasons"])


@pytest.mark.asyncio
async def test_crisis_turns_are_not_counted_as_risk_average():
    db = _db()
    for i in range(6):
        db["user_risk_turns"].docs.append(
            {
                "user_id": "stu_a",
                "risk_intensity_score": 9.0,
                "crisis_keywords": ["x"],
                "created_at": NOW - timedelta(hours=i),
            }
        )
    result = await evaluate_user_for_consultation(db, "stu_a", now=NOW)
    assert result["status"] == "NOT_NEEDED"


@pytest.mark.asyncio
async def test_signals_are_per_user():
    db = _db()
    db["session_reports"].docs += [_report("stu_b", 9, 1), _report("stu_b", 9, 2)]
    a = await evaluate_user_for_consultation(db, "stu_a", now=NOW)
    b = await evaluate_user_for_consultation(db, "stu_b", now=NOW)
    assert a["status"] == "NOT_NEEDED"
    assert b["status"] == "REFERRED"
    assert (await latest_evaluation(db, "stu_a"))["status"] == "NOT_NEEDED"


@pytest.mark.asyncio
async def test_students_cannot_evaluate_others_but_staff_can():
    db = _db()
    assert await target_user(db, "stu_a", None) == "stu_a"
    assert await target_user(db, "stu_a", "stu_a") == "stu_a"
    with pytest.raises(PermissionError):
        await target_user(db, "stu_a", "stu_b")
    assert await target_user(db, "staff_1", "stu_b") == "stu_b"


@pytest.mark.asyncio
async def test_manual_override_needs_a_reason_and_is_audited():
    db = _db()
    with pytest.raises(ValueError):
        await manual_override(db, "stu_a", status="REFERRED", reason="", actor="staff_1", now=NOW)
    with pytest.raises(ValueError):
        await manual_override(db, "stu_a", status="MAYBE", reason="reviewed notes", actor="staff_1", now=NOW)
    result = await manual_override(
        db, "stu_a", status="REFERRED", reason="Counselor reviewed the file.", actor="staff_1", now=NOW
    )
    assert result["status"] == "REFERRED"
    assert result["trigger"] == "MANUAL_OVERRIDE"
    audit = db["psychiatric_evaluation_audit"].docs
    override = [row for row in audit if row["action"] == "MANUAL_OVERRIDE"]
    assert override and override[0]["actor"] == "staff_1"
    assert "Counselor reviewed" in override[0]["details"]["reason"]


@pytest.mark.asyncio
async def test_care_context_is_calm_and_internal():
    db = _db()
    assert "No professional-care status" in await build_care_context(db, "stu_a")
    db["session_reports"].docs += [_report("stu_a", 8, 1), _report("stu_a", 8, 2)]
    await evaluate_user_for_consultation(db, "stu_a", now=NOW)
    text = await build_care_context(db, "stu_a")
    assert "Professional care has been recommended" in text
    assert "Do not push" in text
    prompt = format_system_prompt(care_context=text)
    assert "PROFESSIONAL CARE STATUS" in prompt
    assert "never alarm" in prompt
    for row in db["psychiatric_evaluations"].docs:
        assert "signals" in row
    assert "signals" not in text

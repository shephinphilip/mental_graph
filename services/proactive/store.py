"""User-scoped persistence and cooldown for proactive opportunities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from config.config import get_settings
from services.apm import _owner_namespace
from services.proactive.schemas import ProactiveStatus
from services.security import open_text, seal_text

COLLECTION = "proactive_questions"

_DISPATCHED_LIKE = frozenset(
    {
        ProactiveStatus.DISPATCHED.value,
        ProactiveStatus.DELIVERED.value,
        ProactiveStatus.SEEN.value,
        ProactiveStatus.RESPONDED.value,
    }
)


def make_event_id(user_id: str, execution_nonce: str) -> str:
    return f"pq_{_owner_namespace(user_id)}__{execution_nonce}"


def make_execution_nonce(
    user_id: str,
    trigger_type: str,
    topic: str,
    *,
    now: Optional[datetime] = None,
) -> str:
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    window_seconds = max(1.0, float(settings.PROACTIVE_COOLDOWN_HOURS) * 3600.0)
    window = int(moment.timestamp() // window_seconds)
    slug = " ".join((topic or "").casefold().split())[:80]
    material = f"{user_id}|{trigger_type}|{slug}|{window}"
    return sha256(material.encode("utf-8")).hexdigest()[:24]


def _normalize_question(text: str) -> str:
    return " ".join((text or "").casefold().split())


async def ensure_proactive_indexes(db: AsyncIOMotorDatabase) -> None:
    coll = db[COLLECTION]
    await coll.create_index(
        [("user_id", 1), ("event_id", 1)], unique=True, name="uniq_proactive_event"
    )
    await coll.create_index(
        [("user_id", 1), ("execution_nonce", 1)],
        unique=True,
        name="uniq_proactive_nonce",
    )
    await coll.create_index(
        [("user_id", 1), ("status", 1), ("created_at", -1)],
        name="idx_proactive_status_timeline",
    )
    await coll.create_index(
        [("user_id", 1), ("expires_at", 1)], name="idx_proactive_expiry"
    )


async def expire_stale(
    db: AsyncIOMotorDatabase, user_id: str, *, now: Optional[datetime] = None
) -> int:
    moment = now or datetime.now(timezone.utc)
    result = await db[COLLECTION].update_many(
        {
            "user_id": user_id,
            "status": {
                "$in": [
                    ProactiveStatus.CANDIDATE_CREATED.value,
                    ProactiveStatus.APPROVED.value,
                    ProactiveStatus.DISPATCHED.value,
                ]
            },
            "expires_at": {"$lte": moment},
        },
        {
            "$set": {
                "status": ProactiveStatus.EXPIRED.value,
                "suppression_reason": "expired",
                "updated_at": moment,
            }
        },
    )
    count = int(getattr(result, "modified_count", 0) or 0)
    if count:
        from services.proactive.observability import bump

        bump("questions_expired", count)
    return count


async def insert_opportunity(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    event_id: str,
    execution_nonce: str,
    trigger_type: str,
    question: str,
    receptivity_state: str,
    confidence: float,
    risk_state: str,
    language: str,
    script: str,
        source_node_ids: List[str],
        status: str,
        session_id: str = "",
        topic: str = "",
        suppression_reason: str = "",
        now: Optional[datetime] = None,
) -> Dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    settings = get_settings()
    ttl = timedelta(hours=float(settings.PROACTIVE_OPPORTUNITY_TTL_HOURS))
    doc = {
        "user_id": user_id,
        "event_id": event_id,
        "execution_nonce": execution_nonce,
        "session_id": session_id,
        "trigger_type": trigger_type,
        "source_node_ids": list(source_node_ids or []),
        "topic": topic,
        "receptivity_state": receptivity_state,
        "confidence": float(confidence),
        "risk_state": risk_state,
        "language": language,
        "script": script,
        "question": seal_text(question) if question else "",
        "question_fingerprint": _normalize_question(question),
        "status": status,
        "suppression_reason": suppression_reason,
        "created_at": moment,
        "updated_at": moment,
        "dispatched_at": moment if status == ProactiveStatus.DISPATCHED.value else None,
        "delivered_at": None,
        "delivered_message_id": "",
        "responded_at": None,
        "expires_at": moment + ttl,
    }
    try:
        await db[COLLECTION].insert_one(doc)
        return {"doc": doc, "duplicate": False}
    except DuplicateKeyError:
        existing = await db[COLLECTION].find_one(
            {
                "user_id": user_id,
                "$or": [
                    {"event_id": event_id},
                    {"execution_nonce": execution_nonce},
                ],
            }
        )
        return {"doc": existing or doc, "duplicate": True}


async def mark_status(
    db: AsyncIOMotorDatabase,
    user_id: str,
    event_id: str,
    status: str,
    *,
    extra: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> bool:
    moment = now or datetime.now(timezone.utc)
    updates: Dict[str, Any] = {"status": status, "updated_at": moment}
    if status == ProactiveStatus.DISPATCHED.value:
        updates["dispatched_at"] = moment
    if status == ProactiveStatus.DELIVERED.value:
        updates["delivered_at"] = moment
    if status == ProactiveStatus.SEEN.value:
        updates["seen_at"] = moment
    if status == ProactiveStatus.RESPONDED.value:
        updates["responded_at"] = moment
    if extra:
        updates.update(extra)
    result = await db[COLLECTION].update_one(
        {"user_id": user_id, "event_id": event_id},
        {"$set": updates},
    )
    return int(getattr(result, "modified_count", 0) or 0) > 0


async def get_event(
    db: AsyncIOMotorDatabase, user_id: str, event_id: str
) -> Optional[Dict[str, Any]]:
    return await db[COLLECTION].find_one({"user_id": user_id, "event_id": event_id})


def opened_question(doc: Optional[Dict[str, Any]]) -> str:
    if not doc:
        return ""
    return open_text(str(doc.get("question") or ""))


async def recent_events(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    hours: float,
    now: Optional[datetime] = None,
    statuses: Optional[List[str]] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(hours=float(hours))
    query: Dict[str, Any] = {"user_id": user_id, "created_at": {"$gte": since}}
    if statuses:
        query["status"] = {"$in": statuses}
    cursor = db[COLLECTION].find(query).sort("created_at", -1).limit(limit)
    return await cursor.to_list(length=limit)


async def pending_for_user(
    db: AsyncIOMotorDatabase, user_id: str, *, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    await expire_stale(db, user_id, now=now)
    moment = now or datetime.now(timezone.utc)
    return await db[COLLECTION].find_one(
        {
            "user_id": user_id,
            "status": ProactiveStatus.APPROVED.value,
            "expires_at": {"$gt": moment},
        },
        sort=[("created_at", -1)],
    )


async def cooldown_reason(
    db: AsyncIOMotorDatabase,
    user_id: str,
    *,
    topic: str = "",
    question: str = "",
    now: Optional[datetime] = None,
) -> str:
    settings = get_settings()
    moment = now or datetime.now(timezone.utc)
    await expire_stale(db, user_id, now=moment)

    ignored = await db[COLLECTION].find_one(
        {
            "user_id": user_id,
            "status": ProactiveStatus.RESPONDED.value,
            "outcome": "ignored",
            "responded_at": {
                "$gte": moment
                - timedelta(hours=float(settings.PROACTIVE_IGNORE_SUPPRESSION_HOURS))
            },
        }
    )
    if ignored:
        return "ignored_recent_outreach"

    dispatched = await recent_events(
        db,
        user_id,
        hours=float(settings.PROACTIVE_COOLDOWN_HOURS),
        now=moment,
        statuses=list(_DISPATCHED_LIKE),
        limit=5,
    )
    if dispatched:
        return "cooldown_active"

    window_events = await recent_events(
        db,
        user_id,
        hours=float(settings.PROACTIVE_MAX_ATTEMPTS_WINDOW_HOURS),
        now=moment,
        statuses=list(_DISPATCHED_LIKE),
        limit=int(settings.PROACTIVE_MAX_ATTEMPTS_PER_WINDOW) + 2,
    )
    if len(window_events) >= int(settings.PROACTIVE_MAX_ATTEMPTS_PER_WINDOW):
        return "max_attempts_window"

    lookback = await recent_events(
        db,
        user_id,
        hours=float(settings.PROACTIVE_DUPLICATE_LOOKBACK_HOURS),
        now=moment,
        statuses=list(_DISPATCHED_LIKE | {ProactiveStatus.APPROVED.value}),
        limit=12,
    )
    fingerprint = _normalize_question(question)
    topic_key = " ".join((topic or "").casefold().split())
    for row in lookback:
        if fingerprint and row.get("question_fingerprint") == fingerprint:
            return "duplicate_question"
        if topic_key and topic_key in str(row.get("question_fingerprint") or ""):
            return "repeated_topic"
        if topic_key and topic_key == str(row.get("topic") or "").casefold():
            return "repeated_topic"
    return ""


async def latest_awaiting_response(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str = "",
    *,
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    query: Dict[str, Any] = {
        "user_id": user_id,
        "status": {
            "$in": [
                ProactiveStatus.DELIVERED.value,
                ProactiveStatus.SEEN.value,
            ]
        },
    }
    if session_id:
        query["session_id"] = session_id
    return await db[COLLECTION].find_one(query, sort=[("created_at", -1)])

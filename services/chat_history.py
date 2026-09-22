"""
chat_history.py — Idempotent message persistence for Zenark sessions
====================================================================

Owns how welcome + chat turns are stored so ``/chat/welcome``,
``/chat/send``, ``/chat/stream``, and session resume share one contract:

* Every document has ``message_id``, ``role``, ``message_kind``, ``seq``,
  ``created_at``, and an ``idempotency_key``.
* Welcome is identified by ``message_kind == "welcome"`` (not by text).
* User and assistant turns are separate records; never overwrite each other.
* Chronological order is ``(created_at, seq)`` so equal-second clocks cannot
  scramble assistant vs user.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from services.security import decrypt_payload, encrypt_payload

logger = logging.getLogger(__name__)

KIND_WELCOME = "welcome"
KIND_CHAT = "chat"

HISTORY_SORT_ASC: List[Tuple[str, int]] = [("created_at", 1), ("seq", 1)]
HISTORY_SORT_DESC: List[Tuple[str, int]] = [("created_at", -1), ("seq", -1)]


async def ensure_message_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create idempotency / welcome uniqueness indexes (safe to re-run)."""
    messages = db["messages"]
    await messages.create_index(
        [("session_id", 1), ("user_id", 1), ("created_at", -1), ("seq", -1)],
        name="session_user_created_seq",
    )
    await messages.create_index(
        [("session_id", 1), ("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        partialFilterExpression={"idempotency_key": {"$type": "string"}},
        name="session_user_idempotency_unique",
    )
    await messages.create_index(
        [("session_id", 1), ("user_id", 1), ("message_kind", 1)],
        unique=True,
        partialFilterExpression={"message_kind": KIND_WELCOME},
        name="session_welcome_unique",
    )


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:24]


def _new_message_id() -> str:
    return str(uuid.uuid4())


async def _next_seq(db: AsyncIOMotorDatabase, user_id: str, session_id: str) -> int:
    latest = await db["messages"].find_one(
        {"session_id": session_id, "user_id": user_id},
        sort=HISTORY_SORT_DESC,
        projection={"seq": 1},
    )
    if not latest:
        return 1
    try:
        return int(latest.get("seq") or 0) + 1
    except (TypeError, ValueError):
        return 1


async def get_welcome_message(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the session welcome document if one exists (by kind, not text)."""
    doc = await db["messages"].find_one(
        {
            "session_id": session_id,
            "user_id": user_id,
            "message_kind": KIND_WELCOME,
            "role": "assistant",
        }
    )
    return doc


async def session_has_any_messages(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
) -> bool:
    doc = await db["messages"].find_one(
        {"session_id": session_id, "user_id": user_id},
        projection={"_id": 1},
    )
    return doc is not None


async def get_latest_message(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
) -> Optional[Dict[str, Any]]:
    return await db["messages"].find_one(
        {"session_id": session_id, "user_id": user_id},
        sort=HISTORY_SORT_DESC,
    )


async def load_session_messages(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
    *,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Load decrypted chronological history for LLM / resume."""
    cursor = (
        db["messages"]
        .find(
            {"session_id": session_id, "user_id": user_id},
            {
                "role": 1,
                "content": 1,
                "created_at": 1,
                "seq": 1,
                "message_id": 1,
                "message_kind": 1,
            },
        )
        .sort(HISTORY_SORT_DESC)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    docs.reverse()
    return docs


def decrypt_message_doc(doc: Dict[str, Any]) -> Dict[str, str]:
    return {
        "role": doc.get("role", "user"),
        "content": decrypt_payload(doc.get("content", "")),
        "message_id": str(doc.get("message_id") or ""),
        "message_kind": str(doc.get("message_kind") or KIND_CHAT),
    }


async def find_completed_user_turn(
    db: AsyncIOMotorDatabase,
    user_id: str,
    session_id: str,
    user_message: str,
) -> Optional[Dict[str, Any]]:
    """
    If this exact user text was already answered after the same anchor tip,
    return that assistant document (idempotent retry after success).
    """
    docs = await load_session_messages(db, user_id, session_id, limit=6)
    if len(docs) < 2:
        return None

    last = docs[-1]
    prev = docs[-2]
    if (
        prev.get("role") == "user"
        and last.get("role") == "assistant"
        and last.get("message_kind") != KIND_WELCOME
        and decrypt_payload(prev.get("content", "")) == user_message
    ):
        return last
    return None


async def persist_message(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    role: str,
    content: str,
    message_kind: str = KIND_CHAT,
    idempotency_key: str,
    created_at: Optional[datetime] = None,
    seq: Optional[int] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], bool]:
    """
    Insert one history record.

    Returns ``(document, inserted)``. ``inserted`` is False when the
    idempotency key already existed (duplicate suppressed).
    """
    now = created_at or datetime.now(timezone.utc)
    sequence = seq if seq is not None else await _next_seq(db, user_id, session_id)
    message_id = _new_message_id()
    doc: Dict[str, Any] = {
        "message_id": message_id,
        "session_id": session_id,
        "user_id": user_id,
        "role": role,
        "message_kind": message_kind,
        "content": encrypt_payload(content),
        "idempotency_key": idempotency_key,
        "seq": sequence,
        "created_at": now,
    }
    if extra:
        doc.update(extra)

    try:
        await db["messages"].insert_one(doc)
        return doc, True
    except DuplicateKeyError:
        existing = await db["messages"].find_one(
            {
                "session_id": session_id,
                "user_id": user_id,
                "idempotency_key": idempotency_key,
            }
        )
        if existing:
            logger.info(
                "Duplicate message suppressed — session=%s key=%s",
                session_id,
                idempotency_key,
            )
            return existing, False
        # Welcome partial unique index race: fetch by kind
        if message_kind == KIND_WELCOME:
            welcome = await get_welcome_message(db, user_id, session_id)
            if welcome:
                return welcome, False
        raise


def welcome_idempotency_key(session_id: str, user_id: str) -> str:
    return f"{session_id}:{user_id}:welcome"


def turn_idempotency_keys(
    session_id: str,
    user_id: str,
    anchor_message_id: str,
    user_message: str,
) -> Tuple[str, str]:
    digest = _content_hash(user_message)
    base = f"{session_id}:{user_id}:after:{anchor_message_id or 'none'}:{digest}"
    return f"{base}:user", f"{base}:assistant"


async def persist_welcome_message(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    content: str,
) -> Tuple[Dict[str, Any], bool]:
    """Insert welcome once per session. Returns existing doc if already present."""
    existing = await get_welcome_message(db, user_id, session_id)
    if existing:
        return existing, False

    return await persist_message(
        db,
        user_id=user_id,
        session_id=session_id,
        role="assistant",
        content=content,
        message_kind=KIND_WELCOME,
        idempotency_key=welcome_idempotency_key(session_id, user_id),
        seq=1,
        created_at=datetime.now(timezone.utc),
    )


async def persist_user_and_assistant(
    db: AsyncIOMotorDatabase,
    *,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_reply: str,
) -> Dict[str, Any]:
    """
    Persist a normal chat exchange as two separate records (user then assistant).

    Idempotent for retries of the same user text after the same pre-turn anchor
    (including retries after the assistant reply was already stored).
    """
    # Fast path: turn already complete at session tip.
    existing_assistant = await find_completed_user_turn(
        db, user_id, session_id, user_message
    )
    if existing_assistant:
        docs = await load_session_messages(db, user_id, session_id, limit=4)
        user_doc = docs[-2] if len(docs) >= 2 else None
        return {
            "user_doc": user_doc,
            "assistant_doc": existing_assistant,
            "user_inserted": False,
            "assistant_inserted": False,
        }

    recent = await load_session_messages(db, user_id, session_id, limit=4)
    tip = recent[-1] if recent else None

    # Orphan user (persisted, assistant failed): reuse that user row's anchor.
    if (
        tip
        and tip.get("role") == "user"
        and decrypt_payload(tip.get("content", "")) == user_message
    ):
        anchor_id = (
            str(recent[-2].get("message_id") or "none") if len(recent) >= 2 else "none"
        )
        _user_key, assistant_key = turn_idempotency_keys(
            session_id, user_id, anchor_id, user_message
        )
        base_time = tip.get("created_at") or datetime.now(timezone.utc)
        assistant_doc, assistant_inserted = await persist_message(
            db,
            user_id=user_id,
            session_id=session_id,
            role="assistant",
            content=assistant_reply,
            message_kind=KIND_CHAT,
            idempotency_key=assistant_key,
            created_at=base_time + timedelta(milliseconds=1)
            if hasattr(base_time, "year")
            else datetime.now(timezone.utc),
            seq=int(tip.get("seq") or 0) + 1,
        )
        return {
            "user_doc": tip,
            "assistant_doc": assistant_doc,
            "user_inserted": False,
            "assistant_inserted": assistant_inserted,
        }

    anchor_id = str(tip.get("message_id") or "") if tip else "none"
    user_key, assistant_key = turn_idempotency_keys(
        session_id, user_id, anchor_id, user_message
    )

    base_time = datetime.now(timezone.utc)
    base_seq = await _next_seq(db, user_id, session_id)

    user_doc, user_inserted = await persist_message(
        db,
        user_id=user_id,
        session_id=session_id,
        role="user",
        content=user_message,
        message_kind=KIND_CHAT,
        idempotency_key=user_key,
        created_at=base_time,
        seq=base_seq,
    )
    assistant_doc, assistant_inserted = await persist_message(
        db,
        user_id=user_id,
        session_id=session_id,
        role="assistant",
        content=assistant_reply,
        message_kind=KIND_CHAT,
        idempotency_key=assistant_key,
        created_at=(user_doc.get("created_at") or base_time) + timedelta(milliseconds=1),
        seq=int(user_doc.get("seq") or base_seq) + 1,
    )
    return {
        "user_doc": user_doc,
        "assistant_doc": assistant_doc,
        "user_inserted": user_inserted,
        "assistant_inserted": assistant_inserted,
    }

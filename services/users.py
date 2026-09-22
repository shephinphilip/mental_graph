"""
services/users.py — User identity lookups and login.

Password hashes are verified here. Password values are never included
in documents returned to the API or the LLM prompt.
"""

from __future__ import annotations

import hashlib
import base64
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings

logger = logging.getLogger(__name__)

_PUBLIC_USER_PROJECTION = {
    "password": 0,
}

DEMO_PASSWORD_PREFIX = "sha256:"


def hash_password(plain: str) -> str:
    digest = hashlib.sha256(f"zenark::{plain}".encode("utf-8")).hexdigest()
    return f"{DEMO_PASSWORD_PREFIX}{digest}"


def verify_password(plain: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith(DEMO_PASSWORD_PREFIX):
        return hash_password(plain) == stored
    # Legacy plaintext dummy rows (should not exist after seed)
    return stored == plain


def public_user_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Strip secrets and normalise identifiers for the client."""
    if not doc:
        return {}
    user_id = doc.get("user_id") or str(doc.get("_id", ""))
    return {
        "user_id": user_id,
        "email": doc.get("email"),
        "name": doc.get("name"),
        "class": doc.get("class") or doc.get("grade") or doc.get("class_level"),
        "school": doc.get("school"),
        "isActive": bool(doc.get("isActive", True)),
        "roles": doc.get("roles") or [],
        "preferred_language": doc.get("preferred_language") or "ENGLISH",
        "current_risk_level": doc.get("current_risk_level"),
        "age": doc.get("age"),
        "chief_concern": doc.get("chief_concern"),
        "board": doc.get("board") or doc.get("school_board"),
        "personalization_consent": bool(doc.get("personalization_consent", False)),
    }


def issue_access_token(user_id: str) -> str:
    """Create a compact signed bearer token without exposing profile data."""
    settings = get_settings()
    payload = {
        "sub": user_id,
        "exp": int(time.time()) + settings.AUTH_TOKEN_TTL_SECONDS,
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        settings.AUTH_SIGNING_SECRET.encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    sig = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{body}.{sig}"


def verify_access_token(token: str) -> str:
    """Validate signature and expiry, returning the authenticated user ID."""
    try:
        body, supplied_sig = token.split(".", 1)
        expected = hmac.new(
            get_settings().AUTH_SIGNING_SECRET.encode("utf-8"),
            body.encode("ascii"),
            hashlib.sha256,
        ).digest()
        supplied = base64.urlsafe_b64decode(supplied_sig + "=" * (-len(supplied_sig) % 4))
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("invalid signature")
        payload = json.loads(
            base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8")
        )
        if int(payload["exp"]) < int(time.time()):
            raise ValueError("token expired")
        return str(payload["sub"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid or expired access token") from exc


async def get_by_identifier(
    db: AsyncIOMotorDatabase,
    identifier: str,
    *,
    include_password: bool = False,
) -> Optional[Dict[str, Any]]:
    """Lookup by email, user_id, or 24-hex ObjectId."""
    if not identifier:
        return None
    ident = identifier.strip()
    clauses: list = [{"user_id": ident}, {"email": ident.lower()}, {"email": ident}]
    if ObjectId.is_valid(ident) and len(ident) == 24:
        clauses.append({"_id": ObjectId(ident)})

    projection = None if include_password else _PUBLIC_USER_PROJECTION
    doc = await db["users"].find_one({"$or": clauses}, projection)
    return doc


async def is_active(db: AsyncIOMotorDatabase, identifier: str) -> bool:
    doc = await get_by_identifier(db, identifier)
    if not doc:
        return False
    return bool(doc.get("isActive", True))


async def authenticate(
    db: AsyncIOMotorDatabase, email: str, password: str
) -> Optional[Dict[str, Any]]:
    doc = await get_by_identifier(db, email, include_password=True)
    if not doc:
        return None
    if not doc.get("isActive", True):
        return None
    if not verify_password(password, doc.get("password") or ""):
        return None
    logger.info("User authenticated — email=%s user_id=%s", doc.get("email"), doc.get("user_id"))
    return public_user_view(doc)


async def set_preferred_language(
    db: AsyncIOMotorDatabase, identifier: str, language: str
) -> Optional[Dict[str, Any]]:
    doc = await get_by_identifier(db, identifier, include_password=True)
    if not doc:
        return None
    await db["users"].update_one(
        {"_id": doc["_id"]},
        {
            "$set": {
                "preferred_language": language,
                "preferred_language_updated_at": datetime.now(timezone.utc),
            }
        },
    )
    updated = await get_by_identifier(db, identifier)
    return public_user_view(updated) if updated else None


async def set_personalization_consent(
    db: AsyncIOMotorDatabase, user_id: str, enabled: bool
) -> bool:
    result = await db["users"].update_one(
        {"user_id": user_id, "isActive": {"$ne": False}},
        {
            "$set": {
                "personalization_consent": enabled,
                "personalization_consent_updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return bool(result.matched_count)

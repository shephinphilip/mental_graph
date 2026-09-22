"""Authentication token, signup, and ownership tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.users import (
    authenticate,
    hash_password,
    issue_access_token,
    register_user,
    verify_access_token,
    verify_password,
)


def test_access_token_round_trip():
    token = issue_access_token("student_123")
    assert verify_access_token(token) == "student_123"


def test_access_token_rejects_tampering():
    token = issue_access_token("student_123")
    body, signature = token.split(".")
    tampered = f"{body[:-1]}A.{signature}"
    with pytest.raises(ValueError, match="invalid or expired"):
        verify_access_token(tampered)


def test_password_hash_is_not_plaintext():
    stored = hash_password("Zenark@123")
    assert stored != "Zenark@123"
    assert verify_password("Zenark@123", stored)
    assert not verify_password("wrong", stored)


@pytest.mark.asyncio
async def test_register_user_persists_hashed_password_and_public_view():
    inserted = {}

    async def insert_one(doc):
        inserted["doc"] = doc
        return MagicMock(inserted_id="x")

    db = MagicMock()
    db["users"].find_one = AsyncMock(return_value=None)
    db["users"].insert_one = AsyncMock(side_effect=insert_one)

    user = await register_user(
        db,
        email="New.User@Example.com",
        password="SecurePass1",
        name="New User",
    )

    assert user["email"] == "new.user@example.com"
    assert user["name"] == "New User"
    assert user["user_id"].startswith("usr_")
    assert "password" not in user
    assert inserted["doc"]["password"].startswith("sha256:")
    assert verify_password("SecurePass1", inserted["doc"]["password"])


@pytest.mark.asyncio
async def test_register_user_rejects_duplicate_email():
    db = MagicMock()
    db["users"].find_one = AsyncMock(
        return_value={"user_id": "existing", "email": "taken@zenark.demo"}
    )

    with pytest.raises(ValueError, match="already exists"):
        await register_user(
            db, email="taken@zenark.demo", password="SecurePass1"
        )


@pytest.mark.asyncio
async def test_authenticate_after_register_shape():
    password_hash = hash_password("SecurePass1")
    db = MagicMock()
    db["users"].find_one = AsyncMock(
        return_value={
            "user_id": "usr_abc",
            "email": "me@zenark.demo",
            "name": "Me",
            "password": password_hash,
            "isActive": True,
        }
    )
    user = await authenticate(db, "me@zenark.demo", "SecurePass1")
    assert user is not None
    assert user["user_id"] == "usr_abc"
    assert "password" not in user

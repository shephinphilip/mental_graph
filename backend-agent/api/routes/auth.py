"""Authentication routes: login and signup."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from config.config import logger
from database import get_db
from schemas import LoginRequest, LoginResponse, SignupRequest
from backend_core.users import authenticate, issue_access_token, register_user

router = APIRouter(tags=["auth"])


def _session_payload(user: dict) -> LoginResponse:
    return LoginResponse(
        user_id=user["user_id"],
        access_token=issue_access_token(user["user_id"]),
        email=user.get("email"),
        name=user.get("name"),
        student_class=user.get("class"),
        school=user.get("school"),
        preferred_language=user.get("preferred_language"),
        age=user.get("age"),
        chief_concern=user.get("chief_concern"),
        board=user.get("board"),
        personalization_consent=user.get("personalization_consent", False),
    )


@router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    """Authenticate by email. Password is never returned."""
    user = await authenticate(db, payload.email, payload.password)
    if not user:
        logger.warning("Login failed")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _session_payload(user)


@router.post("/auth/signup", response_model=LoginResponse, status_code=201)
async def signup(
    payload: SignupRequest,
    background_tasks: BackgroundTasks,
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Create a new account. Returns the same session payload as login."""
    try:
        user = await register_user(
            db,
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
    except ValueError as exc:
        detail = str(exc)
        status = 409 if "already exists" in detail.lower() else 400
        raise HTTPException(status_code=status, detail=detail) from exc

    from services.student_profile import enqueue_profile_refresh

    enqueue_profile_refresh(background_tasks, db, user["user_id"])
    return _session_payload(user)

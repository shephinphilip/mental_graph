"""Language preference route. Canonical source: ``users.preferred_language``.

Path is declared without the ``/api`` prefix; the aggregate router adds ``/api``
for the legacy mount and ``/api/v1`` for the versioned mount.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from api.deps import authenticated_user_id
from database import get_db
from schemas import LanguagePreferenceRequest
from services.users import set_preferred_language

router = APIRouter(tags=["language"])


@router.post("/language")
async def update_preferred_language(
    payload: LanguagePreferenceRequest,
    user_id: str = Depends(authenticated_user_id),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """Store the signed-in user's language. The body user id is not the target."""
    from services.language_preferences import language_write_target

    try:
        language_write_target(user_id, payload.user_id)
        updated = await set_preferred_language(db, user_id, payload.language)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Active user not found")
    return {
        "preferred_language": updated.get("preferred_language"),
        "preferred_language_updated_at": updated.get("preferred_language_updated_at"),
    }

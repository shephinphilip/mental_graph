"""Who may run an evaluation for someone else."""

from __future__ import annotations

from services.users import get_by_identifier
from dashboard.identity import same_school, tenant_key

STAFF_ROLES = {"staff", "admin", "counselor", "psychiatrist"}


async def is_staff(db, user_id: str) -> bool:
    user = await get_by_identifier(db, user_id)
    if not isinstance(user, dict):
        return False
    roles = {str(role).strip().lower() for role in (user.get("roles") or [])}
    return bool(roles & STAFF_ROLES)


async def target_user(db, authenticated_id: str, claimed_user_id: str | None) -> str:
    """A student evaluates only themself. School-assigned staff may name a same-school user."""
    claimed = (claimed_user_id or "").strip()
    if not claimed or claimed == authenticated_id:
        return authenticated_id
    if await is_staff(db, authenticated_id):
        actor = await get_by_identifier(db, authenticated_id)
        target = await get_by_identifier(db, claimed)
        actor_key = tenant_key(actor) if isinstance(actor, dict) else None
        if not actor_key:
            raise PermissionError("Cannot evaluate another user")
        if not isinstance(target, dict) or not same_school(actor_key, target):
            raise PermissionError("Cannot evaluate another user")
        return claimed
    raise PermissionError("Cannot evaluate another user")

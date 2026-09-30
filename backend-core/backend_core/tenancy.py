"""School tenant key. One implementation for consultation and the dashboard.

The tenant key comes from the authenticated user document. A client-supplied
school id is never consulted.

* ``school_id`` present → tenant is ``id:<school_id>``
* otherwise the exact stripped ``school`` name → ``name:<school>``
"""

from __future__ import annotations

from typing import Optional


def school_id_of(user: dict | None) -> str:
    return str((user or {}).get("school_id") or "").strip()


def school_name_of(user: dict | None) -> str:
    return " ".join(str((user or {}).get("school") or "").split())


def tenant_key(user: dict | None) -> Optional[str]:
    sid = school_id_of(user)
    if sid:
        return "id:" + sid
    name = school_name_of(user)
    if name:
        return "name:" + name
    return None


def same_school(actor_key: str, user: dict) -> bool:
    return bool(actor_key) and tenant_key(user) == actor_key

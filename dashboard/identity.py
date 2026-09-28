"""School, class, grade, and subject identity.

The tenant key comes from the authenticated user document. A client-supplied
school id is never consulted.

* ``school_id`` present → tenant is ``id:<school_id>``
* otherwise the exact stripped ``school`` name → ``name:<school>``

A name-only account does not see rows that already carry a different
``school_id``, and an id-scoped account does not see name-only rows.
That keeps a later school-id migration from silently merging two schools
that happen to share a label.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

_SLUG = re.compile(r"[^a-z0-9]+")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_YEAR = re.compile(r"^\d{4}-\d{2}$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def clean_token(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not _ID.match(text):
        raise ValueError(f"Invalid {label}")
    return text


def clean_year(value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not _YEAR.match(text):
        raise ValueError("academic_year must look like 2025-26")
    return text


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


def school_query(user: dict) -> dict:
    """Candidate filter. Callers still apply ``same_school`` before use."""
    clauses = []
    sid = school_id_of(user)
    name = school_name_of(user)
    if sid:
        clauses.append({"school_id": sid})
    if name:
        clauses.append({"school": name})
    if not clauses:
        return {"user_id": "__no_school__"}
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


def class_label(user: dict) -> str:
    return " ".join(
        str(user.get("class") or user.get("grade") or user.get("class_level") or "").split()
    )


def slug(text: str) -> str:
    lowered = _SLUG.sub("-", str(text or "").strip().lower()).strip("-")
    return lowered or "unassigned"


def class_id_of(user: dict) -> str:
    label = class_label(user)
    return slug(label) if label else "unassigned"


def grade_id_of(user: dict) -> str:
    label = class_label(user)
    match = re.search(r"\d+", label)
    if match:
        return match.group(0)
    return slug(label) if label else "unassigned"


def subject_id_of(name: Any) -> str:
    text = " ".join(str(name or "").split())
    return slug(text) if text else "unassigned"


def subject_name(name: Any) -> str:
    return " ".join(str(name or "").split())


def as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return None


def academic_year_of(value: datetime) -> str:
    """Indian school year, April through March."""
    start = value.year if value.month >= 4 else value.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def calendar_academic_year(now: Optional[datetime] = None) -> str:
    return academic_year_of(now or datetime.now(timezone.utc))


def mark_year(mark: dict) -> Optional[str]:
    explicit = " ".join(str(mark.get("academic_year") or "").split())
    if _YEAR.match(explicit):
        return explicit
    when = as_datetime(mark.get("exam_date"))
    if when is None:
        return None
    return academic_year_of(when)


def isoformat(value: Any) -> Optional[str]:
    when = as_datetime(value)
    if when is None:
        return None
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def listed(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for item in value:
        text = " ".join(str(item or "").split())
        if text and text not in out:
            out.append(text)
    return out

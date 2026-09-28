"""Who may open the school dashboard.

Students, teachers, generic staff, and psychiatrists are not principals.
Counselors are included because the dashboard notifies them.
"""

from __future__ import annotations

from typing import Any, FrozenSet, Iterable

DASHBOARD_ROLES = frozenset({"principal", "school_admin", "admin", "counselor"})
NON_STUDENT_ROLES = DASHBOARD_ROLES | frozenset({"teacher", "staff", "psychiatrist"})
PRIMARY_ROLE_ORDER = ("principal", "school_admin", "admin", "counselor")

PERMISSIONS = (
    "dashboard.read",
    "dashboard.students.read",
    "dashboard.teachers.read",
    "dashboard.risk.read",
    "dashboard.interventions.write",
    "dashboard.notifications.read",
    "dashboard.reports.write",
    "dashboard.assistant.use",
    "dashboard.settings.write",
    "dashboard.events.read",
)


def role_set(user: dict | None) -> FrozenSet[str]:
    raw = (user or {}).get("roles") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Iterable):
        return frozenset()
    return frozenset(
        str(role).strip().lower() for role in raw if str(role).strip()
    )


def primary_role(roles: FrozenSet[str]) -> str:
    for role in PRIMARY_ROLE_ORDER:
        if role in roles:
            return role
    return ""


def can_access_dashboard(roles: FrozenSet[str]) -> bool:
    return bool(roles & DASHBOARD_ROLES)


def is_student_account(user: dict) -> bool:
    return not (role_set(user) & NON_STUDENT_ROLES)


def is_teacher_account(user: dict) -> bool:
    return "teacher" in role_set(user)


def is_active(user: dict) -> bool:
    return user.get("isActive", True) is not False


def assignee_allowed(user: dict) -> bool:
    roles = role_set(user)
    return bool(roles & (DASHBOARD_ROLES | {"teacher"}))


def scrub_roles(roles: FrozenSet[str]) -> list[str]:
    return sorted(roles)

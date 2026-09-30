"""Agent-facing re-export. The implementation lives in ``backend_core.deps``."""

from backend_core.deps import assert_owner, authenticated_user_id, task_http

__all__ = ["authenticated_user_id", "assert_owner", "task_http"]

"""Static boundaries between the three applications."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


def _repository_root() -> Path:
    """Monolith root when this file sits in backend-agent/tests, else this repo."""
    here = Path(__file__).resolve()
    outer = here.parents[2]
    if (outer / "docker-compose.staging.yml").is_file() and (outer / "backend-agent").is_dir():
        return outer
    return here.parents[1]


ROOT = _repository_root()


def _agent_root() -> Path:
    nested = ROOT / "backend-agent"
    if (nested / "app.py").is_file():
        return nested
    return ROOT

AGENT_FORBIDDEN = {"dashboard"}
CORE_FORBIDDEN = {
    "services",
    "api",
    "dashboard",
    "exam_buddy_guardrails",
    "consultation",
    "journaling",
    "student_memory",
    "tracking",
    "sleep",
    "tasks",
    "meditation",
    "prompts",
    "agent_indexes",
    "workers",
    "reports",
    "integrations",
    "schemas",
    "mental_schema",
}
DASHBOARD_FORBIDDEN = CORE_FORBIDDEN - {"dashboard"}


def _roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".", 1)[0])
    return found


def _py_files(folder: Path):
    skip = {"__pycache__", "tests"}
    for path in folder.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        yield path


def _offenders(folder: Path, forbidden: set[str]) -> list[str]:
    hits = []
    for path in _py_files(folder):
        found = _roots(path) & forbidden
        if found:
            hits.append(f"{path.relative_to(ROOT)} imports {sorted(found)}")
    return hits


def test_backend_core_does_not_import_agent_or_dashboard():
    folder = ROOT / "backend-core"
    if not folder.is_dir():
        pytest.skip("backend-core source is not inside this checkout")
    hits = _offenders(folder, CORE_FORBIDDEN)
    assert hits == []


def test_dashboard_does_not_import_agent_internals():
    folder = ROOT / "dashboard"
    if not folder.is_dir():
        pytest.skip("dashboard source is not inside this checkout")
    hits = _offenders(folder, DASHBOARD_FORBIDDEN)
    assert hits == []


def test_backend_agent_does_not_import_dashboard():
    hits = _offenders(_agent_root(), AGENT_FORBIDDEN)
    assert hits == []


def _paths(application):
    from fastapi.routing import APIWebSocketRoute

    found = set()
    for route in application.router.routes:
        if isinstance(route, APIWebSocketRoute):
            found.add(route.path)
            continue
        contexts = getattr(route, "effective_route_contexts", None)
        if contexts:
            for ctx in contexts():
                found.add(ctx.path)
            continue
        path = getattr(route, "path", None)
        if path:
            found.add(path)
    return found


def test_each_product_route_has_one_owner():
    from app import app as agent_app

    agent_paths = _paths(agent_app)
    assert "/chat/send" in agent_paths
    assert "/api/v1/chat/send" in agent_paths
    assert "/auth/login" in agent_paths
    assert "/ws/psychiatrist-voice" in agent_paths
    assert not any(path.startswith("/api/v1/dashboard") for path in agent_paths)
    try:
        from dashboard_app import app as dashboard_application
    except ModuleNotFoundError:
        pytest.skip("dashboard application is not on the import path")
    dashboard_paths = _paths(dashboard_application)
    assert "/api/v1/dashboard/overview" in dashboard_paths
    assert "/chat/send" not in dashboard_paths

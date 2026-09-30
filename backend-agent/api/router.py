"""Assemble every HTTP router once.

Compatibility mounts keep the paths Streamlit and existing tests already call.
``/api/v1`` remounts the same handlers so new clients can version without a
second implementation.
"""

from __future__ import annotations

from fastapi import APIRouter

from api.routes import (
    auth,
    chat,
    consultation,
    exam_buddy,
    health,
    journal,
    language,
    meditation,
    memory,
    patterns,
    proactive,
    reports,
    sleep,
    streaming,
    tasks,
    tracking,
    voice,
)


def _mount_domain(router: APIRouter, *, api_prefix: str) -> None:
    """Chat/auth/journal/consultation keep their historic unprefixed paths.

    Everything that historically lived under ``/api/...`` is mounted at
    ``api_prefix`` (``/api`` or ``/api/v1``).
    """
    router.include_router(auth.router)
    router.include_router(chat.router)
    router.include_router(streaming.router)
    router.include_router(journal.router)
    router.include_router(consultation.router)
    router.include_router(voice.router)
    router.include_router(language.router, prefix=api_prefix)
    router.include_router(memory.router, prefix=api_prefix)
    router.include_router(proactive.router, prefix=api_prefix)
    router.include_router(patterns.router, prefix=api_prefix)
    router.include_router(sleep.router, prefix=api_prefix)
    router.include_router(tracking.router, prefix=api_prefix)
    router.include_router(tasks.router, prefix=api_prefix)
    router.include_router(reports.router, prefix=api_prefix)
    router.include_router(meditation.router, prefix=api_prefix)
    router.include_router(exam_buddy.router, prefix=api_prefix)


def build_api_router() -> APIRouter:
    root = APIRouter()
    root.include_router(health.router)
    _mount_domain(root, api_prefix="/api")

    versioned = APIRouter()
    versioned.include_router(health.router)
    _mount_domain(versioned, api_prefix="")
    # The school dashboard is a separate application. It is not mounted here.
    root.include_router(versioned, prefix="/api/v1")
    return root

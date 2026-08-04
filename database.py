"""
database.py — Async Database Connection Management
===================================================

Manages the full lifecycle of two database connections:

  1. **MongoDB** (via Motor async driver)
     - Stores: chat messages, user profiles, mood logs, habit events,
       user insights, action card logs.
     - Connection pool is created at startup and closed at shutdown.

  2. **Neo4j** (via the official async neo4j driver)
     - Stores: the user-centric knowledge / emotional graph used by
       Graph RAG to enrich LLM context.
     - Graph uniqueness constraints are created (idempotently) on
       every startup via ``ensure_graph_constraints``.

Both clients are stored on ``app.state`` so they remain in memory for
the process lifetime and are shared across all requests without
re-connecting on every call.

FastAPI dependency functions ``get_db`` and ``get_neo4j_driver`` let
route handlers receive the correct client via ``Depends(...)``.
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager — handles startup and shutdown.

    On **startup**:
      - Creates a Motor (async MongoDB) client and attaches it to
        ``app.state.db`` and ``app.state.mongo_client``.
      - Creates an async Neo4j driver and attaches it to
        ``app.state.neo4j_driver``.
      - Runs ``ensure_graph_constraints`` to create Neo4j uniqueness
        constraints.  Failure here is non-fatal (constraints may
        already exist, or Neo4j may be temporarily unavailable).

    On **shutdown**:
      - Gracefully closes the Neo4j driver session pool.
      - Closes the Motor client (releases all pooled TCP connections).

    Parameters
    ----------
    app : FastAPI
        The application instance whose ``.state`` namespace is used to
        share database clients across requests.

    Yields
    ------
    None
        Control is yielded back to FastAPI while the application runs.

    Raises
    ------
    Exception
        Any unhandled exception during client initialisation will
        propagate and prevent the application from starting.
    """
    # Lazy import: avoid loading the Neo4j driver at module import time
    # (prevents DLL/extension loading unless the app actually starts).
    from neo4j import AsyncGraphDatabase

    settings = get_settings()

    # ── MongoDB ──────────────────────────────────────────────────────────────
    # Motor is non-blocking; the connection pool is lazily established on the
    # first real database operation, not here.
    logger.info("Connecting to MongoDB at %s", settings.MONGODB_URI)
    mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(settings.MONGODB_URI)
    app.state.db = mongo_client[settings.DATABASE_NAME]
    app.state.mongo_client = mongo_client
    logger.info("MongoDB client ready — database=%s", settings.DATABASE_NAME)

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    # The driver manages its own async session pool internally.
    logger.info("Connecting to Neo4j at %s", settings.NEO4J_URI)
    neo4j_driver = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    app.state.neo4j_driver = neo4j_driver
    logger.info("Neo4j driver ready — URI=%s", settings.NEO4J_URI)

    # Ensure uniqueness constraints exist on all node label types.
    # Wrapped in try/except because:
    #   - Constraints may already exist from a previous startup.
    #   - Neo4j may be temporarily unreachable (startup race condition).
    # The graph will still work; it just won't have index enforcement.
    try:
        from services.graph_rag import ensure_graph_constraints
        await ensure_graph_constraints(neo4j_driver)
    except Exception:
        logger.warning(
            "Could not create Neo4j constraints — graph features may be limited. "
            "This is non-fatal; the application will continue.",
            exc_info=True,  # Include full traceback in warning log
        )

    # ── Application is running ────────────────────────────────────────────────
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    # These must be awaited to flush any pending async I/O before the event
    # loop closes.
    logger.info("Shutting down — closing database connections.")
    await neo4j_driver.close()
    mongo_client.close()
    logger.info("Database connections closed.")


def get_db(request: Request) -> AsyncIOMotorDatabase:
    """
    FastAPI dependency — injects the shared Motor database instance.

    This function is intended to be used with ``Depends(get_db)`` in
    route function signatures.  It returns the ``AsyncIOMotorDatabase``
    object stored on ``app.state`` during startup.

    Parameters
    ----------
    request : Request
        The current FastAPI request object.  FastAPI injects this
        automatically when this function is used as a dependency.

    Returns
    -------
    AsyncIOMotorDatabase
        The shared Motor database handle for the configured database.

    Raises
    ------
    AttributeError
        If called before the lifespan startup has completed (i.e.,
        ``app.state.db`` has not been set).  This should not happen in
        normal operation.
    """
    return request.app.state.db


def get_neo4j_driver(request: Request):
    """
    FastAPI dependency — injects the shared Neo4j async driver instance.

    This function is intended to be used with ``Depends(get_neo4j_driver)``
    in route function signatures.  It returns the ``AsyncDriver`` object
    stored on ``app.state`` during startup.

    Parameters
    ----------
    request : Request
        The current FastAPI request object.  FastAPI injects this
        automatically when this function is used as a dependency.

    Returns
    -------
    neo4j.AsyncDriver
        The shared Neo4j async driver with a session pool.

    Raises
    ------
    AttributeError
        If called before the lifespan startup has completed (i.e.,
        ``app.state.neo4j_driver`` has not been set).  This should not
        happen in normal operation.
    """
    return request.app.state.neo4j_driver

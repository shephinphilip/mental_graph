"""
database.py — Async Database Connection Management
===================================================

Manages the full lifecycle of the MongoDB connection.

**v0.4.0 migration note**: Neo4j has been removed as a dependency.
MongoDB is now the single primary database for all application data,
including the knowledge graph (``graph_nodes``, ``graph_relationships``).

On **startup**:
  - Creates a Motor (async MongoDB) client.
  - Attaches the client and database handle to ``app.state``.
  - Calls ``ensure_graph_constraints`` to idempotently create compound
    indexes on the graph collections.

On **shutdown**:
  - Closes the Motor client (releases all pooled TCP connections).

FastAPI dependency functions
----------------------------
``get_db``
    Injects the shared ``AsyncIOMotorDatabase`` handle into route handlers.
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
      - Creates a Motor (async MongoDB) client with connection pool
        configured for production load.
      - Attaches ``app.state.db`` and ``app.state.mongo_client``.
      - Creates MongoDB graph indexes via ``ensure_graph_constraints``
        (idempotent — safe to run on every startup).

    On **shutdown**:
      - Closes the Motor client and releases all pooled TCP connections.

    Parameters
    ----------
    app : FastAPI
        The application instance whose ``.state`` namespace is used to
        share the database client across requests.

    Yields
    ------
    None
        Control is yielded back to FastAPI while the application runs.
    """
    settings = get_settings()

    # ── MongoDB ──────────────────────────────────────────────────────────────
    # Motor is non-blocking; the connection pool is lazily established on the
    # first real database operation, not here.
    logger.info("Connecting to MongoDB at %s", settings.MONGODB_URI)
    mongo_client: AsyncIOMotorClient = AsyncIOMotorClient(
        settings.MONGODB_URI,
        # Connection pool settings for production throughput
        maxPoolSize=100,
        minPoolSize=5,
        serverSelectionTimeoutMS=5_000,
        socketTimeoutMS=30_000,
        connectTimeoutMS=5_000,
        retryWrites=True,
    )
    app.state.db = mongo_client[settings.DATABASE_NAME]
    app.state.mongo_client = mongo_client
    logger.info("MongoDB client ready — database=%s", settings.DATABASE_NAME)

    # ── MongoDB Graph Indexes ─────────────────────────────────────────────────
    # Ensure compound indexes exist on graph_nodes and graph_relationships.
    # Wrapped in try/except because:
    #   - Indexes may already exist from a previous startup (idempotent).
    #   - MongoDB Atlas may be temporarily unreachable at startup.
    # The application will still function; graph context retrieval will be
    # slower without indexes, but never broken.
    try:
        from services.graph_rag import ensure_graph_constraints
        await ensure_graph_constraints(app.state.db)
        from services.apm import ensure_apm_indexes
        await ensure_apm_indexes(app.state.db)
        from services.chat_history import ensure_message_indexes
        await ensure_message_indexes(app.state.db)
    except Exception:
        logger.warning(
            "Could not create MongoDB memory indexes — memory features may be slower. "
            "This is non-fatal; the application will continue.",
            exc_info=True,
        )

    # ── Application is running ────────────────────────────────────────────────
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down — closing MongoDB connection.")
    mongo_client.close()
    logger.info("MongoDB connection closed.")


def get_db(request: Request) -> AsyncIOMotorDatabase:
    """
    FastAPI dependency — injects the shared Motor database instance.

    Intended for use with ``Depends(get_db)`` in route function signatures.

    Parameters
    ----------
    request : Request
        The current FastAPI request object.

    Returns
    -------
    AsyncIOMotorDatabase
        The shared Motor database handle for the configured database.

    Raises
    ------
    AttributeError
        If called before lifespan startup has completed (should not happen
        in normal operation).
    """
    return request.app.state.db

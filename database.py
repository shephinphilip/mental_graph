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

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from starlette.requests import HTTPConnection

from config.config import logger
from db.indexes import ensure_all_indexes
from db.mongo import create_mongo_client, database_name


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
    # ── MongoDB ──────────────────────────────────────────────────────────────
    # One client / pool per process. Domain modules keep owning their indexes.
    logger.info("Connecting to MongoDB")
    mongo_client: AsyncIOMotorClient = create_mongo_client()
    app.state.db = mongo_client[database_name()]
    app.state.mongo_client = mongo_client
    logger.info("MongoDB client ready — database=%s", database_name())

    try:
        await ensure_all_indexes(app.state.db)
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


def get_db(connection: HTTPConnection) -> AsyncIOMotorDatabase:
    """
    FastAPI dependency — injects the shared Motor database instance.

    Works for HTTP requests and WebSocket connections (both are
    ``HTTPConnection``), so voice and chat share the same override in tests.
    """
    return connection.app.state.db

"""
services/graph_rag.py — Graph RAG Service (MongoDB-backed)
===========================================================

**This file was migrated from Neo4j to MongoDB in v0.4.0.**

All Neo4j Cypher queries, MERGE statements, and driver interactions have
been removed.  The implementation is now backed entirely by
``services/mongo_graph.py``, which uses MongoDB ``graph_nodes`` and
``graph_relationships`` collections with ``$graphLookup`` traversal.

Public interface
----------------
The public function signatures have been updated: ``driver`` (neo4j.AsyncDriver)
is replaced by ``db`` (AsyncIOMotorDatabase) to reflect the new backend.

  ensure_graph_constraints(db)
      Called once at startup.  Creates compound indexes on ``graph_nodes``
      and ``graph_relationships`` (idempotent).

  get_user_emotional_graph(db, user_id)
      Traverses up to ``GRAPH_TRAVERSAL_DEPTH`` hops from the User root node.
      Returns a natural-language bullet-fact string for the LLM system prompt.
      Falls back gracefully if the graph is empty or an error occurs.

  upsert_graph_tuples(db, user_id, tuples)
      Upserts a batch of LLM-extracted ``GraphTuple`` objects into MongoDB.
      All operations are idempotent.

Migration notes
---------------
* Node identity is now deterministic via ``_make_node_id()`` in
  ``services/mongo_graph.py``.  Repeated extraction of the same fact
  produces exactly one document — no duplicates.
* User isolation is enforced at the MongoDB query level via ``user_id``
  filters in every read and write operation.
* The ``_format_graph_for_prompt`` and ``_node_key_property`` helper
  functions that were used by ``test_graph_rag.py`` are re-exported here
  for backward test compatibility.  New code should use
  ``services.mongo_graph`` directly.
"""

import logging
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from schemas import GraphTuple
from services.mongo_graph import (
    ensure_graph_indexes,
    get_user_graph_context,
    upsert_graph_tuples as _mongo_upsert_graph_tuples,
    _format_graph_records,
    _make_node_id,
    _NODE_NAME_FIELD,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Startup — Index / Constraint Initialisation
# ════════════════════════════════════════════════════════════════════════════


async def ensure_graph_constraints(db: AsyncIOMotorDatabase) -> None:
    """
    Create MongoDB indexes on graph collections (replaces Neo4j constraints).

    Delegates to ``services.mongo_graph.ensure_graph_indexes``.
    Called once at application startup; idempotent.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The shared Motor database handle.
    """
    await ensure_graph_indexes(db)
    logger.info("Graph indexes ensured via MongoDB (formerly Neo4j constraints).")


# ════════════════════════════════════════════════════════════════════════════
# READ PATH — Graph Context Retrieval
# ════════════════════════════════════════════════════════════════════════════


async def get_user_emotional_graph(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """
    Retrieve and format the user's emotional/relational subgraph.

    Delegates to ``services.mongo_graph.get_user_graph_context``, which
    executes a user-scoped ``$graphLookup`` aggregation on MongoDB.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The Motor async database handle.
    user_id : str
        The unique user identifier.

    Returns
    -------
    str
        Newline-separated bullet facts, or
        ``"No relational graph data available yet."`` if the graph is empty
        or an error occurs.

    Example output
    --------------
    ::

        • Emotional state: Anxiety (intensity: 8) — via: experiences
        • Trigger: Work Deadlines — via: triggered by
        • Coping tool: 8-Min Body Scan — via: tried tool
    """
    return await get_user_graph_context(db, user_id)


# ════════════════════════════════════════════════════════════════════════════
# WRITE PATH — Graph Memory Writer
# ════════════════════════════════════════════════════════════════════════════


async def upsert_graph_tuples(
    db: AsyncIOMotorDatabase,
    user_id: str,
    tuples: List[GraphTuple],
) -> int:
    """
    Upsert a list of graph tuples (relational facts) into MongoDB.

    Delegates to ``services.mongo_graph.upsert_graph_tuples``.
    All upserts are idempotent: repeated extraction of the same fact
    updates ``updated_at`` but creates no duplicate documents.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The Motor async database handle.
    user_id : str
        The owning user's identifier.
    tuples : list[GraphTuple]
        Validated ``GraphTuple`` Pydantic models from the extraction pipeline.

    Returns
    -------
    int
        Number of tuples successfully upserted.
    """
    return await _mongo_upsert_graph_tuples(db, user_id, tuples)


# ════════════════════════════════════════════════════════════════════════════
# Backward-Compatibility Shims (for test_graph_rag.py)
# ════════════════════════════════════════════════════════════════════════════
# These functions were previously defined here and are referenced in tests.
# They now delegate to the MongoDB implementation.


def _format_graph_for_prompt(records: List[Dict[str, Any]]) -> str:
    """
    Shim: format raw aggregation records into bullet facts.

    Previously formatted Neo4j Cypher result records.  Now delegates to
    ``_format_graph_records`` in ``services.mongo_graph``, which accepts
    MongoDB aggregation records with the same shape.

    The record shape expected by the new tests:
    ::

        {
            "target_type": "Emotion",
            "target_name": "Anxiety",
            "relation": "EXPERIENCES",
            "target_props": {"intensity": 8},
            "rel_props": {},
            "rel_user_id": "<user_id>",
        }

    Returns
    -------
    str
        Formatted bullet-fact string.
    """
    return _format_graph_records(records)


def _node_key_property(label: str) -> str:
    """
    Shim: return the canonical key property name for a node label.

    Previously drove the Neo4j MERGE predicate key.  Now returns the same
    mapping used by ``_NODE_NAME_FIELD`` in ``services.mongo_graph`` for
    the MongoDB upsert identity scheme.

    Parameters
    ----------
    label : str
        The node label string (e.g. ``"User"``, ``"Emotion"``).

    Returns
    -------
    str
        The canonical key property name.
    """
    return _NODE_NAME_FIELD.get(label, "name")

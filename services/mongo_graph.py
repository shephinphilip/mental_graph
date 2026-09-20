"""
services/mongo_graph.py — MongoDB Graph Layer (replaces Neo4j)
==============================================================

Implements the user-scoped therapeutic knowledge graph using two MongoDB
collections:

  graph_nodes
      Stores individual graph nodes (User, Emotion, Trigger, CopingTool,
      Entity, Event, Session).  Each node is uniquely identified by the
      compound ``(user_id, node_id)`` pair.

  graph_relationships
      Stores directed edges between nodes.  Each relationship is uniquely
      identified by ``(user_id, from_node_id, relation, to_node_id)``.

Design decisions
----------------
* **User isolation at every query level** — every find/update/aggregate
  includes ``user_id`` as a mandatory filter.  No query path exists that
  can return data across user boundaries.

* **Deterministic node IDs** — ``node_id`` is derived from the node type
  and canonical name via ``_make_node_id()``, making all upserts
  idempotent by construction.  Repeated extraction of the same fact
  produces exactly one document in ``graph_nodes`` and one in
  ``graph_relationships``.

* **$graphLookup with restrictSearchWithMatch** — the traversal uses
  MongoDB's native graph lookup operator with a ``user_id`` restriction so
  the database engine never returns foreign-user documents even during
  recursive expansion.

* **Depth is configurable** — ``GRAPH_TRAVERSAL_DEPTH`` from settings
  controls the maximum hop count.  A hard cap of 30 result nodes is
  enforced to bound prompt-injection size.

* **Fail-safe reads** — ``get_user_graph_context`` catches all exceptions
  and returns a graceful fallback string so the conversation pipeline is
  never blocked by a graph error.

Public API
----------
  ensure_graph_indexes(db)            → None
  upsert_node(db, ...)                → str   (the node_id)
  upsert_relationship(db, ...)        → None
  get_user_graph_context(db, user_id) → str
  upsert_graph_tuples(db, user_id, tuples) → int
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_settings
from schemas import GraphTuple

logger = logging.getLogger(__name__)

# ── Allowed relationship types (whitelist for traversal safety) ───────────────
_ALLOWED_RELATIONS = frozenset(
    [
        "EXPERIENCES",
        "TRIGGERED_BY",
        "ASSOCIATED_WITH",
        "TRIED_TOOL",
        "HELPED_WITH",
        "FOLLOWED_BY",
        "PARTICIPATED_IN",
    ]
)

# ── Node key property mapping (mirrors former Neo4j key map) ─────────────────
# Used to pick the canonical "name" field for each node type.
_NODE_NAME_FIELD: Dict[str, str] = {
    "User": "id",
    "Entity": "name",
    "Event": "title",
    "Emotion": "state",
    "Trigger": "description",
    "CopingTool": "name",
    "Session": "session_id",
}


# ════════════════════════════════════════════════════════════════════════════
# Startup — Index Creation
# ════════════════════════════════════════════════════════════════════════════


async def ensure_graph_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create compound indexes on ``graph_nodes`` and ``graph_relationships``.

    Called once at application startup (``lifespan`` in ``database.py``).
    Each ``create_index`` call is idempotent — existing indexes are silently
    skipped by Motor/pymongo.

    Indexes created
    ---------------
    graph_nodes:
        { user_id: 1, node_id: 1 }  UNIQUE  — primary identity key
        { user_id: 1, node_type: 1 }         — type queries
        { user_id: 1, name: 1 }              — name look-ups

    graph_relationships:
        { user_id: 1, from_node_id: 1 }
        { user_id: 1, to_node_id: 1 }
        { user_id: 1, relation: 1 }
        { user_id: 1, from_node_id: 1, relation: 1 }  — selective traversal
        { user_id: 1, from_node_id: 1, relation: 1, to_node_id: 1 }  UNIQUE

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The shared Motor database handle.

    Returns
    -------
    None
    """
    nodes = db["graph_nodes"]
    rels = db["graph_relationships"]

    # ── graph_nodes indexes ───────────────────────────────────────────────────
    await nodes.create_index(
        [("user_id", 1), ("node_id", 1)],
        unique=True,
        name="uniq_user_node",
    )
    await nodes.create_index(
        [("user_id", 1), ("node_type", 1)],
        name="idx_user_node_type",
    )
    await nodes.create_index(
        [("user_id", 1), ("name", 1)],
        name="idx_user_node_name",
    )

    # ── graph_relationships indexes ───────────────────────────────────────────
    await rels.create_index(
        [("user_id", 1), ("from_node_id", 1)],
        name="idx_user_from",
    )
    await rels.create_index(
        [("user_id", 1), ("to_node_id", 1)],
        name="idx_user_to",
    )
    await rels.create_index(
        [("user_id", 1), ("relation", 1)],
        name="idx_user_relation",
    )
    await rels.create_index(
        [("user_id", 1), ("from_node_id", 1), ("relation", 1)],
        name="idx_user_from_relation",
    )
    await rels.create_index(
        [("user_id", 1), ("from_node_id", 1), ("relation", 1), ("to_node_id", 1)],
        unique=True,
        name="uniq_user_edge",
    )

    logger.info("MongoDB graph indexes ensured on graph_nodes and graph_relationships.")


# ════════════════════════════════════════════════════════════════════════════
# Identity Helpers
# ════════════════════════════════════════════════════════════════════════════


def _make_node_id(node_type: str, name: str, user_id: Optional[str] = None) -> str:
    """
    Derive a deterministic, stable ``node_id`` from the node type and name.

    Rules
    -----
    * ``User`` nodes use the actual ``user_id`` so the root node is always
      ``"user_{user_id}"``.
    * All other node types normalise the name to lowercase, replace spaces and
      hyphens with underscores, and strip non-alphanumeric characters, then
      prefix with the lowercased type.

    Examples
    --------
    ::

        _make_node_id("Emotion", "Anxiety")          → "emotion_anxiety"
        _make_node_id("Trigger", "Work Deadlines")   → "trigger_work_deadlines"
        _make_node_id("User", "User", user_id="u1")  → "user_u1"
        _make_node_id("CopingTool", "8-Min Body Scan") → "copingtool_8_min_body_scan"

    Parameters
    ----------
    node_type : str
        The node label (e.g. ``"Emotion"``).
    name : str
        The canonical name value extracted from the conversation.
    user_id : str, optional
        Required (and only used) when ``node_type == "User"``.

    Returns
    -------
    str
        A URL-safe, deterministic identifier string.
    """
    if node_type == "User":
        if not user_id:
            raise ValueError("user_id is required for User node_id generation")
        return f"user_{user_id}"

    slug = name.lower()
    slug = slug.replace(" ", "_").replace("-", "_")
    slug = re.sub(r"[^\w]", "", slug)  # strip remaining non-word chars
    return f"{node_type.lower()}_{slug}"


def generate_node_id(user_id: str, node_type: str, name: str) -> str:
    """
    Public helper to generate deterministic node_id for user, node_type, and name.
    """
    return _make_node_id(node_type=node_type, name=name, user_id=user_id)


def _node_canonical_name(node_type: str, t: "GraphTuple") -> str:
    """
    Return the canonical name string for a graph tuple's source or target node.

    Uses the same priority mapping as the former Neo4j implementation.

    Parameters
    ----------
    node_type : str
        The label string (e.g. ``"Emotion"``).
    t : GraphTuple
        The graph tuple (used to extract the appropriate name field).

    Returns
    -------
    str
        The canonical name string for the node.
    """
    return t.source_node if node_type == t.source_label.value else t.target_node


# ════════════════════════════════════════════════════════════════════════════
# Write Path — Upsert Operations
# ════════════════════════════════════════════════════════════════════════════


async def upsert_node(
    db: AsyncIOMotorDatabase,
    user_id: str,
    node_id: str,
    node_type: str,
    name: str,
    properties: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Idempotently create or update a single graph node.

    Uses MongoDB ``update_one`` with ``upsert=True`` so repeated calls with
    the same ``(user_id, node_id)`` pair are safe and cheap — only
    ``updated_at`` is touched on repeat calls with the same data.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The shared Motor database handle.
    user_id : str
        The owning user's identifier.  **Always required.**
    node_id : str
        The deterministic node identifier from ``_make_node_id()``.
    node_type : str
        The node label (e.g. ``"Emotion"``).
    name : str
        The canonical display name of the node.
    properties : dict, optional
        Additional metadata to merge onto the node document.

    Returns
    -------
    str
        The ``node_id`` that was upserted (useful for chaining).

    Raises
    ------
    motor errors
        If the MongoDB ``update_one`` call fails (network, write concern).
        The caller (``upsert_graph_tuples``) logs and skips these.
    """
    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user_id,  # Always set — user isolation key
        "node_id": node_id,
        "node_type": node_type,
        "name": name,
        "properties": properties or {},
        "updated_at": now,
    }
    await db["graph_nodes"].update_one(
        # Filter: unique identity — scoped by user_id for isolation
        {"user_id": user_id, "node_id": node_id},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now},  # Only set on first creation
        },
        upsert=True,
    )
    return node_id


async def upsert_relationship(
    db: AsyncIOMotorDatabase,
    user_id: str,
    from_node_id: str,
    from_node_type: str,
    relation: str,
    to_node_id: str,
    to_node_type: str,
    properties: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Idempotently create or update a directed relationship between two nodes.

    The unique identity of a relationship is:
    ``(user_id, from_node_id, relation, to_node_id)``

    Repeated calls update ``updated_at`` and merge any new properties but do
    not create duplicate edges.

    Security note
    -------------
    ``user_id`` is included in both the filter and the document body.  This
    ensures that no relationship can be created or retrieved across user
    boundaries, even in the presence of accidental node_id collisions across
    users (which cannot happen given the deterministic ID scheme, but is
    defended against in depth).

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The shared Motor database handle.
    user_id : str
        The owning user's identifier.
    from_node_id : str
        The deterministic ID of the source node.
    from_node_type : str
        Label of the source node (for display/formatting).
    relation : str
        The relationship type (must be in ``_ALLOWED_RELATIONS``).
    to_node_id : str
        The deterministic ID of the target node.
    to_node_type : str
        Label of the target node.
    properties : dict, optional
        Additional metadata to merge onto the relationship document.

    Returns
    -------
    None

    Raises
    ------
    ValueError
        If ``relation`` is not in ``_ALLOWED_RELATIONS`` (whitelist guard).
    motor errors
        If the MongoDB ``update_one`` call fails.
    """
    if relation not in _ALLOWED_RELATIONS:
        raise ValueError(
            f"Relationship type '{relation}' is not allowed. "
            f"Allowed: {sorted(_ALLOWED_RELATIONS)}"
        )

    now = datetime.now(timezone.utc)
    doc = {
        "user_id": user_id,  # Always set — user isolation key
        "from_node_id": from_node_id,
        "from_node_type": from_node_type,
        "relation": relation,
        "to_node_id": to_node_id,
        "to_node_type": to_node_type,
        "properties": properties or {},
        "updated_at": now,
    }
    await db["graph_relationships"].update_one(
        # Unique filter — scoped by user_id for isolation
        {
            "user_id": user_id,
            "from_node_id": from_node_id,
            "relation": relation,
            "to_node_id": to_node_id,
        },
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


# ════════════════════════════════════════════════════════════════════════════
# Read Path — Graph Context Retrieval
# ════════════════════════════════════════════════════════════════════════════


async def get_user_graph_context(
    db: AsyncIOMotorDatabase,
    user_id: str,
    depth: Optional[int] = None,
) -> str:
    """
    Retrieve and format the user's emotional/relational subgraph from MongoDB.

    Uses MongoDB's ``$graphLookup`` aggregation stage to traverse the
    ``graph_relationships`` collection starting from the user's root node.
    The ``restrictSearchWithMatch`` parameter enforces user isolation at the
    database engine level — the lookup can never return documents from a
    different user even at intermediate traversal depths.

    Returns a natural-language bullet-point string ready for injection into
    the LLM system prompt.  Falls back gracefully if the graph is empty or
    an error occurs.

    Security guarantee
    ------------------
    Every document returned by this function has ``user_id == user_id``.
    This is enforced by:
      1. The ``$match`` stage filtering ``graph_nodes`` to ``user_id``.
      2. The ``restrictSearchWithMatch: { "user_id": user_id }`` parameter
         on ``$graphLookup`` restricting all recursive expansions.
      3. A post-lookup Python-level filter (defence in depth).

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The shared Motor database handle.
    user_id : str
        The unique user identifier.  **Mandatory.**

    Returns
    -------
    str
        Newline-separated bullet facts, or
        ``"No relational graph data available yet."`` if the graph is empty
        or an error occurs.
    """
    settings = get_settings()
    depth = depth if depth is not None else settings.GRAPH_TRAVERSAL_DEPTH
    root_node_id = _make_node_id("User", "User", user_id=user_id)

    # Pipeline:
    # 1. Find the root User node (scoped to user_id).
    # 2. $graphLookup expands relationships up to `depth` hops.
    #    restrictSearchWithMatch enforces user isolation at DB level.
    # 3. $unwind the connected relationships array.
    # 4. $lookup enriches each connected node.
    # 5. $limit caps result set to prevent prompt bloat.
    pipeline = [
        # Stage 1: Start from this user's root node only
        {
            "$match": {
                "user_id": user_id,          # User-scoped — mandatory
                "node_id": root_node_id,
            }
        },
        # Stage 2: Traverse outgoing relationships up to `depth` hops.
        # restrictSearchWithMatch is the database-level user isolation guard.
        {
            "$graphLookup": {
                "from": "graph_relationships",
                "startWith": "$node_id",
                "connectFromField": "to_node_id",
                "connectToField": "from_node_id",
                "as": "connected_rels",
                "maxDepth": depth - 1,         # 0-indexed: depth-1 hops
                "depthField": "hop_depth",
                "restrictSearchWithMatch": {
                    "user_id": user_id,        # Isolation enforced inside the DB
                    "relation": {"$in": list(_ALLOWED_RELATIONS)},
                },
            }
        },
        # Stage 3: Explode the relationships array (one doc per edge)
        {"$unwind": {"path": "$connected_rels", "preserveNullAndEmptyArrays": False}},
        # Stage 4: Limit before expensive lookups
        {"$limit": 30},
        # Stage 5: Enrich with target node details
        {
            "$lookup": {
                "from": "graph_nodes",
                "let": {
                    "tid": "$connected_rels.to_node_id",
                    "uid": "$connected_rels.user_id",
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$node_id", "$$tid"]},
                                    # Defence-in-depth user isolation on enrichment
                                    {"$eq": ["$user_id", "$$uid"]},
                                ]
                            }
                        }
                    }
                ],
                "as": "target_node",
            }
        },
        {"$unwind": {"path": "$target_node", "preserveNullAndEmptyArrays": True}},
        # Stage 6: Project only what we need for formatting
        {
            "$project": {
                "_id": 0,
                "relation": "$connected_rels.relation",
                "hop_depth": "$connected_rels.hop_depth",
                "target_type": "$connected_rels.to_node_type",
                "target_name": "$target_node.name",
                "target_props": "$target_node.properties",
                "rel_props": "$connected_rels.properties",
                # Isolation audit field — used in post-lookup defence
                "rel_user_id": "$connected_rels.user_id",
            }
        },
    ]

    try:
        cursor = db["graph_nodes"].aggregate(pipeline)
        records = await cursor.to_list(length=30)

        if not records:
            return "No relational graph data available yet."

        # Defence-in-depth: filter out any document whose user_id doesn't match.
        # This should be mathematically impossible given the pipeline above,
        # but is included as a final safeguard for security-critical data.
        records = [r for r in records if r.get("rel_user_id") == user_id]

        if not records:
            return "No relational graph data available yet."

        return _format_graph_records(records)

    except Exception as exc:
        logger.warning(
            "Graph context retrieval failed for user=%s — falling back to empty context: %s",
            user_id,
            exc,
            exc_info=True,
        )
        return "No relational graph data available yet."


def _format_graph_records(records: List[Dict[str, Any]]) -> str:
    """
    Convert MongoDB aggregation records into natural-language bullet facts.

    Each record corresponds to one relationship edge in the user's subgraph.
    Produces the same human-readable format as the former Neo4j implementation.

    Parameters
    ----------
    records : list[dict]
        Aggregation result documents from ``get_user_graph_context()``.

    Returns
    -------
    str
        Newline-joined bullet facts, or
        ``"No relational graph data available yet."`` if no facts can be formed.
    """
    facts: List[str] = []
    seen: set = set()

    for rec in records:
        target_type = rec.get("target_type", "")
        target_name = rec.get("target_name") or "unknown"
        relation = rec.get("relation", "")
        rel_props = rec.get("rel_props") or {}
        target_props = rec.get("target_props") or {}

        # Build fact parts
        parts = [target_name]

        intensity = rel_props.get("intensity") or target_props.get("intensity")
        if intensity is not None:
            parts.append(f"(intensity: {intensity})")

        status = target_props.get("status")
        if status:
            parts.append(f"[{status}]")

        if relation:
            chain_str = relation.replace("_", " ").lower()
            parts.append(f"— via: {chain_str}")

        # Node type → human-readable prefix (same mapping as Neo4j version)
        if target_type == "Emotion":
            prefix = "Emotional state"
        elif target_type == "Trigger":
            prefix = "Trigger"
        elif target_type == "Entity":
            prefix = "Related person/thing"
        elif target_type == "Event":
            prefix = "Life event"
        elif target_type == "CopingTool":
            prefix = "Coping tool"
        elif target_type == "Session":
            prefix = "Session"
        else:
            prefix = "Connected"

        fact = f"• {prefix}: {' '.join(parts)}"
        if fact not in seen:
            seen.add(fact)
            facts.append(fact)

    return "\n".join(facts) if facts else "No relational graph data available yet."


# Public alias for prompt formatting tests
format_graph_for_prompt = _format_graph_records


# ════════════════════════════════════════════════════════════════════════════
# Batch Write Path — Graph Tuple Upsert
# ════════════════════════════════════════════════════════════════════════════


async def upsert_graph_tuples(
    db: AsyncIOMotorDatabase,
    user_id: str,
    tuples: List[GraphTuple],
) -> int:
    """
    Upsert a list of LLM-extracted graph tuples into MongoDB.

    For each tuple:
    1. Generates deterministic ``node_id`` values for source and target.
    2. Upserts the source node into ``graph_nodes``.
    3. Upserts the target node into ``graph_nodes``.
    4. Upserts the directed relationship into ``graph_relationships``.
    5. Enriches relationship properties with ``source_session_id`` and
       ``source_message_id`` if provided in ``tuple.properties``.

    All operations are idempotent.  Running the same tuple twice touches
    ``updated_at`` but creates no duplicate documents.

    Invalid tuples (unknown relationship type, missing fields) are logged
    and skipped rather than aborting the batch.

    Parameters
    ----------
    db : AsyncIOMotorDatabase
        The shared Motor database handle.
    user_id : str
        The owning user's identifier.
    tuples : list[GraphTuple]
        Validated Pydantic GraphTuple models from the extraction pipeline.

    Returns
    -------
    int
        Number of tuples successfully upserted.
    """
    if not tuples:
        return 0

    # Ensure the User root node exists before upserting relationships.
    # This mirrors the ``MERGE (u:User {id: $user_id})`` in the former Neo4j path.
    user_node_id = _make_node_id("User", "User", user_id=user_id)
    await upsert_node(
        db,
        user_id=user_id,
        node_id=user_node_id,
        node_type="User",
        name=user_id,
    )

    upserted = 0
    for t in tuples:
        try:
            src_type = t.source_label.value   # e.g. "User"
            tgt_type = t.target_label.value   # e.g. "Emotion"
            relation = t.relationship.value   # e.g. "EXPERIENCES"

            # Derive deterministic node IDs
            src_id = (
                user_node_id
                if src_type == "User"
                else _make_node_id(src_type, t.source_node)
            )
            tgt_id = (
                user_node_id
                if tgt_type == "User"
                else _make_node_id(tgt_type, t.target_node)
            )

            src_name = user_id if src_type == "User" else t.source_node
            tgt_name = user_id if tgt_type == "User" else t.target_node

            # Upsert source node
            await upsert_node(
                db,
                user_id=user_id,
                node_id=src_id,
                node_type=src_type,
                name=src_name,
            )

            # Upsert target node
            await upsert_node(
                db,
                user_id=user_id,
                node_id=tgt_id,
                node_type=tgt_type,
                name=tgt_name,
            )

            # Upsert the relationship (raises ValueError if relation not allowed)
            await upsert_relationship(
                db,
                user_id=user_id,
                from_node_id=src_id,
                from_node_type=src_type,
                relation=relation,
                to_node_id=tgt_id,
                to_node_type=tgt_type,
                properties=t.properties,
            )

            upserted += 1

        except ValueError as exc:
            # Relationship type validation failure — log and skip
            logger.warning(
                "Skipping graph tuple %s -[%s]-> %s — validation error: %s",
                t.source_node,
                t.relationship,
                t.target_node,
                exc,
            )
        except Exception:
            # Unexpected error (DB connectivity, etc.) — log and skip
            logger.warning(
                "Failed to upsert graph tuple: %s -[%s]-> %s",
                t.source_node,
                t.relationship,
                t.target_node,
                exc_info=True,
            )

    logger.info(
        "Upserted %d/%d graph tuples for user=%s",
        upserted,
        len(tuples),
        user_id,
    )
    return upserted

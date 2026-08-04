"""
services/graph_rag.py — Neo4j Graph RAG Service
================================================

Provides two complementary paths for interacting with the therapeutic
knowledge graph stored in Neo4j:

READ PATH — Graph Context Retrieval
-------------------------------------
``get_user_emotional_graph(driver, user_id)``

  Traverses up to ``GRAPH_TRAVERSAL_DEPTH`` hops (Cypher variable-length
  path) from the ``User`` node, following all seven therapeutic relationship
  types.  Formats the resulting records as natural-language bullet facts
  and returns them as a string ready for injection into the LLM system
  prompt.  Example output::

      • Emotional state: Anxiety (intensity: 8) — via: experiences
      • Trigger: Work Deadlines — via: experiences → triggered by
      • Coping tool: 8-Min Body Scan — tool: Body Scan

  On any failure (Neo4j unavailable, user node not found, query timeout),
  returns a graceful fallback string so the conversation continues without
  graph context.

WRITE PATH — Graph Memory Writer
----------------------------------
``upsert_graph_tuples(driver, user_id, tuples)``

  Takes a list of ``GraphTuple`` Pydantic models (extracted by the LLM
  from a conversation turn) and upserts them into Neo4j using ``MERGE``
  statements.  Uses idempotent node and relationship creation so repeated
  calls for the same facts are safe.

STARTUP — Uniqueness Constraints
----------------------------------
``ensure_graph_constraints(driver)``

  Called once at application startup.  Creates ``IF NOT EXISTS`` constraints
  on the unique identifier property of each node label type.  Safe to call
  repeatedly (idempotent).

All Neo4j interactions use the async driver (``neo4j.AsyncDriver``) and
are safe to call from FastAPI async routes and ``asyncio.BackgroundTasks``.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from config import get_settings
from schemas import GraphTuple

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Startup — Uniqueness Constraints
# ════════════════════════════════════════════════════════════════════════════


async def ensure_graph_constraints(driver) -> None:
    """
    Create uniqueness constraints on all therapeutic node label types.

    Called once during application startup (``lifespan`` in
    ``database.py``).  Each ``CREATE CONSTRAINT IF NOT EXISTS`` statement
    is idempotent — it is safe to call this function on a database that
    already has the constraints.

    Constraints created:
    - ``User.id``               — prevents duplicate user nodes
    - ``Entity.name``           — prevents duplicate entity nodes (persons, things)
    - ``Trigger.description``   — prevents duplicate trigger nodes
    - ``CopingTool.name``       — prevents duplicate coping tool nodes

    Note: ``Event``, ``Emotion``, and ``Session`` nodes are not constrained
    here because they may legitimately share property values (e.g. two
    different sessions can both have an ``Emotion`` node named "Anxiety").

    Parameters
    ----------
    driver : neo4j.AsyncDriver
        The Neo4j async driver instance (from ``app.state.neo4j_driver``).

    Returns
    -------
    None

    Raises
    ------
    neo4j.exceptions.Neo4jError
        If the Cypher statements are malformed or if the driver cannot
        connect.  The caller (``lifespan`` in ``database.py``) catches
        and logs this as a non-fatal warning.
    """
    # Each statement uses ``IF NOT EXISTS`` for idempotency
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Trigger) REQUIRE t.description IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (c:CopingTool) REQUIRE c.name IS UNIQUE",
    ]

    async with driver.session() as session:
        for cypher in constraints:
            await session.run(cypher)

    logger.info(
        "Neo4j graph constraints ensured (%d statements executed).",
        len(constraints),
    )


# ════════════════════════════════════════════════════════════════════════════
# READ PATH — Graph Context Retrieval
# ════════════════════════════════════════════════════════════════════════════


async def get_user_emotional_graph(driver, user_id: str) -> str:
    """
    Retrieve and format the user's emotional/relational subgraph from Neo4j.

    Executes a variable-length Cypher path query starting from the
    ``User`` node and traversing up to ``GRAPH_TRAVERSAL_DEPTH`` hops
    through allowed relationship types.  The raw records are converted to
    natural-language bullet facts by ``_format_graph_for_prompt()``.

    Returns a graceful fallback string on any failure so the LLM prompt
    is always fully formed even when Neo4j is unavailable.

    Parameters
    ----------
    driver : neo4j.AsyncDriver
        The Neo4j async driver instance.
    user_id : str
        The unique user identifier (matches ``User.id`` in the graph).

    Returns
    -------
    str
        A newline-separated string of bullet-point facts about the user's
        emotional graph.  Returns ``"No relational graph data available yet."``
        if the graph is empty, the user node does not exist, or Neo4j is
        unreachable.

    Raises
    ------
    None
        All exceptions are caught internally and a fallback is returned.

    Example output
    --------------
    ::

        • Emotional state: Anxiety (intensity: 8) — via: experiences
        • Trigger: Work Deadlines — via: experiences → triggered by
        • Coping tool: 8-Min Body Scan — tool: Body Scan
    """
    settings = get_settings()
    depth = settings.GRAPH_TRAVERSAL_DEPTH

    # Variable-length path query: traverses 1..depth hops from the User node.
    # The WHERE clause restricts traversal to only therapeutic relationship types,
    # preventing unintended cross-graph traversal.
    # ``LIMIT 30`` caps result size to keep prompt injection manageable.
    query = """
    MATCH (u:User {id: $user_id})-[r1*1..$depth]-(connected)
    WHERE ALL(rel IN r1 WHERE type(rel) IN [
        'EXPERIENCES', 'TRIGGERED_BY', 'ASSOCIATED_WITH',
        'TRIED_TOOL', 'HELPED_WITH', 'FOLLOWED_BY', 'PARTICIPATED_IN'
    ])
    WITH DISTINCT connected, r1
    OPTIONAL MATCH (connected)-[r2:HELPED_WITH|TRIED_TOOL]-(tool:CopingTool)
    RETURN
        labels(connected) AS node_labels,
        connected.name       AS name,
        connected.state      AS state,
        connected.description AS description,
        connected.title      AS title,
        connected.intensity  AS intensity,
        connected.status     AS status,
        [rel IN r1 | type(rel)] AS relationship_chain,
        tool.name            AS tool_name
    LIMIT 30
    """

    try:
        async with driver.session() as session:
            result = await session.run(query, user_id=user_id, depth=depth)
            # Collect all records as plain dicts (async iteration over result cursor)
            records = [record.data() async for record in result]

        if not records:
            # User node exists but has no connected subgraph yet
            return "No relational graph data available yet."

        return _format_graph_for_prompt(records)

    except Exception:
        # Log the full traceback but return a fallback — conversation must continue
        logger.warning(
            "Graph context retrieval failed for user=%s — falling back to empty context.",
            user_id,
            exc_info=True,  # Includes full traceback in the warning log
        )
        return "No relational graph data available yet."


def _format_graph_for_prompt(records: List[Dict[str, Any]]) -> str:
    """
    Convert raw Cypher result records into natural-language bullet facts.

    Each record corresponds to one node in the user's subgraph.  The
    function determines a human-readable prefix from the node's label(s),
    assembles a fact string from its properties, and deduplicates using
    a ``seen`` set.

    Parameters
    ----------
    records : list[dict]
        Raw record dicts returned by the Neo4j Cypher query in
        ``get_user_emotional_graph()``.  Each dict has keys:
        ``node_labels``, ``name``, ``state``, ``description``, ``title``,
        ``intensity``, ``status``, ``relationship_chain``, ``tool_name``.

    Returns
    -------
    str
        Newline-joined bullet facts, or
        ``"No relational graph data available yet."`` if no displayable
        facts can be formed from the records.

    Example
    -------
    ::

        facts = _format_graph_for_prompt(records)
        # "• Emotional state: Anxiety (intensity: 8) — via: experiences\\n..."
    """
    facts: List[str] = []
    seen: set = set()  # Used to deduplicate identical fact strings

    for rec in records:
        labels = rec.get("node_labels", [])

        # Determine the best display name for this node by priority:
        # name → state → title → description → "unknown"
        name = (
            rec.get("name")
            or rec.get("state")
            or rec.get("title")
            or rec.get("description")
            or "unknown"
        )

        chain = rec.get("relationship_chain", [])    # List of relationship type strings
        intensity = rec.get("intensity")             # Numeric intensity (optional)
        tool_name = rec.get("tool_name")             # Associated coping tool (optional)
        status = rec.get("status")                   # Node status tag (optional)

        # Build a human-readable fact from the graph path parts
        fact_parts = [name]

        if intensity is not None:
            fact_parts.append(f"(intensity: {intensity})")

        if status:
            # E.g. "[active]", "[resolved]"
            fact_parts.append(f"[{status}]")

        if chain:
            # Convert ["EXPERIENCES", "TRIGGERED_BY"] → "experiences → triggered by"
            chain_str = " → ".join(rel.replace("_", " ").lower() for rel in chain)
            fact_parts.append(f"— via: {chain_str}")

        if tool_name:
            fact_parts.append(f"— tool: {tool_name}")

        # Choose the readable prefix based on the node's label type
        if "Emotion" in labels:
            prefix = "Emotional state"
        elif "Trigger" in labels:
            prefix = "Trigger"
        elif "Entity" in labels:
            prefix = "Related person/thing"
        elif "Event" in labels:
            prefix = "Life event"
        elif "CopingTool" in labels:
            prefix = "Coping tool"
        else:
            prefix = "Connected"

        fact = f"• {prefix}: {' '.join(fact_parts)}"

        # Deduplicate: only add this fact if we haven't seen it before
        if fact not in seen:
            seen.add(fact)
            facts.append(fact)

    return "\n".join(facts) if facts else "No relational graph data available yet."


# ════════════════════════════════════════════════════════════════════════════
# WRITE PATH — Graph Memory Writer
# ════════════════════════════════════════════════════════════════════════════


async def upsert_graph_tuples(
    driver, user_id: str, tuples: List[GraphTuple]
) -> int:
    """
    Upsert a list of graph tuples (relational facts) into Neo4j.

    Each tuple creates or updates:
    - A source node (identified by label + key property)
    - A target node (identified by label + key property)
    - A directed relationship between them (with timestamp + properties)

    Uses ``MERGE`` for idempotency: running the same tuple twice will
    update the relationship's ``last_updated`` timestamp but not create
    a duplicate node or edge.

    Parameters
    ----------
    driver : neo4j.AsyncDriver
        The Neo4j async driver instance.
    user_id : str
        The unique user identifier.  Used as the ``id`` property of the
        ``User`` node and as the source value for User-sourced tuples.
    tuples : list[GraphTuple]
        Validated ``GraphTuple`` Pydantic models from the extraction pipeline.

    Returns
    -------
    int
        The number of tuples successfully upserted.  May be less than
        ``len(tuples)`` if individual tuples fail (errors are logged
        and skipped, not re-raised).

    Raises
    ------
    neo4j.exceptions.Neo4jError
        If the initial ``MERGE (u:User {id: ...})`` fails (e.g. driver
        disconnected).  Individual tuple failures are swallowed.
    """
    if not tuples:
        # Early exit — nothing to upsert; avoids opening a session for nothing
        return 0

    upserted = 0

    async with driver.session() as session:
        # Ensure the User root node exists before upserting any relationships.
        # All tuples may reference the User node as source or target.
        await session.run(
            "MERGE (u:User {id: $user_id})",
            user_id=user_id,
        )

        for t in tuples:
            try:
                # Build the MERGE-based Cypher statement for this tuple
                cypher = _build_upsert_cypher(t)
                # Build the parameter dict for the Cypher statement
                params = _build_upsert_params(user_id, t)
                await session.run(cypher, **params)
                upserted += 1

            except Exception:
                # Log individual tuple failures but continue with remaining tuples.
                # A single malformed tuple should not abort the entire batch.
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


def _build_upsert_cypher(t: GraphTuple) -> str:
    """
    Build a MERGE-based Cypher statement for a single graph tuple.

    The generated Cypher:
    1. ``MERGE`` the source node by (label, identifying property)
    2. ``MERGE`` the target node by (label, identifying property)
    3. ``MERGE`` the directed relationship between them
    4. ``SET`` the relationship's ``last_updated`` timestamp and merge
       any additional ``properties`` from the tuple

    Parameters
    ----------
    t : GraphTuple
        A validated graph tuple whose ``source_label``, ``target_label``,
        and ``relationship`` drive the Cypher template.

    Returns
    -------
    str
        A parameterised Cypher statement.  The actual property values are
        passed separately via ``_build_upsert_params()`` to prevent
        Cypher injection.

    Example
    -------
    For a tuple ``User -[EXPERIENCES]-> Anxiety (Emotion)``::

        MERGE (src:User {id: $src_val})
        MERGE (tgt:Emotion {state: $tgt_val})
        MERGE (src)-[r:EXPERIENCES]->(tgt)
        SET r.last_updated = $now, r += $props
    """
    src_label = t.source_label.value  # e.g. "User"
    tgt_label = t.target_label.value  # e.g. "Emotion"
    rel_type = t.relationship.value   # e.g. "EXPERIENCES"

    # Determine the primary key property for each node label
    src_key = _node_key_property(src_label)
    tgt_key = _node_key_property(tgt_label)

    return (
        f"MERGE (src:{src_label} {{{src_key}: $src_val}})\n"
        f"MERGE (tgt:{tgt_label} {{{tgt_key}: $tgt_val}})\n"
        f"MERGE (src)-[r:{rel_type}]->(tgt)\n"
        f"SET r.last_updated = $now, r += $props"
    )


def _build_upsert_params(user_id: str, t: GraphTuple) -> Dict[str, Any]:
    """
    Build the parameter dictionary for a single upsert Cypher statement.

    Handles the special case where the source or target is a ``User`` node:
    in that case, the ``id`` property value must be the actual ``user_id``
    string rather than the raw ``source_node`` / ``target_node`` string
    (which may be the literal ``"User"`` placeholder).

    Parameters
    ----------
    user_id : str
        The actual Neo4j User node identifier.
    t : GraphTuple
        The graph tuple being upserted.

    Returns
    -------
    dict
        A dict with keys ``src_val``, ``tgt_val``, ``now``, ``props``
        for use as Cypher parameters.
    """
    # For User nodes, use the actual user_id as the property value;
    # for all other node types, use the tuple's node name string.
    src_val = user_id if t.source_label.value == "User" else t.source_node
    tgt_val = user_id if t.target_label.value == "User" else t.target_node

    return {
        "src_val": src_val,
        "tgt_val": tgt_val,
        "now": datetime.now(timezone.utc).isoformat(),  # ISO-8601 timestamp string
        "props": t.properties,  # Merged onto the relationship via SET r += $props
    }


def _node_key_property(label: str) -> str:
    """
    Return the primary key property name for a given Neo4j node label.

    Each node label type uses a different property as its unique identifier
    (defined by the constraints created in ``ensure_graph_constraints``).
    This function provides the mapping used by ``_build_upsert_cypher()``
    to build correct ``MERGE`` predicates.

    Parameters
    ----------
    label : str
        The Neo4j node label string (e.g. ``"User"``, ``"Emotion"``).

    Returns
    -------
    str
        The property name to use as the unique key in the ``MERGE``
        clause.  Defaults to ``"name"`` for unknown labels.

    Examples
    --------
    ::

        _node_key_property("User")      # → "id"
        _node_key_property("Emotion")   # → "state"
        _node_key_property("Trigger")   # → "description"
        _node_key_property("Unknown")   # → "name" (default)
    """
    key_map = {
        "User": "id",                # User.id — uniquely identifies a user
        "Entity": "name",            # Entity.name — person/thing name
        "Event": "title",            # Event.title — event title
        "Emotion": "state",          # Emotion.state — emotion label (e.g. "Anxiety")
        "Trigger": "description",    # Trigger.description — trigger description
        "CopingTool": "name",        # CopingTool.name — tool name
        "Session": "session_id",     # Session.session_id — session identifier
    }
    return key_map.get(label, "name")  # Fallback to "name" for unmapped labels

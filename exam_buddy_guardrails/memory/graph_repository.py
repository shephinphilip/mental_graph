"""Learning-graph storage for Exam Buddy.

The therapeutic graph in ``services/mongo_graph.py`` is a different
whitelist (emotions, triggers, coping tools) and is read into Zenark's
chat prompt. Academic learning facts stay in their own collections so
they cannot leak into that prompt or into another student's graph.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Iterable

from exam_buddy_guardrails.config import (
    MIN_LABEL_LENGTH,
    NODES_COLLECTION,
    RELATIONSHIPS_COLLECTION,
)
from exam_buddy_guardrails.models import NodeType, RelationType

_ALIASES = {
    "quadratic equation": "quadratic equations",
    "quadratic equation problems": "quadratic equations",
    "quadratics": "quadratic equations",
    "linear equation": "linear equations",
    "step by step": "step-by-step explanations",
    "step-by-step": "step-by-step explanations",
    "step by step explanations": "step-by-step explanations",
    "showing the steps": "step-by-step explanations",
}

_FILLERS = ("problems", "problem", "questions", "question")


def canonical_label(raw: str) -> str:
    """Fold spelling and plural variants onto one label."""
    text = (raw or "").casefold().replace("'", "")
    text = text.replace("-", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for filler in _FILLERS:
        text = re.sub(rf"\b{filler}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith(" equation"):
        text += "s"
    return _ALIASES.get(text, text)


def label_tokens(label: str) -> list[str]:
    canonical = canonical_label(label)
    return [part for part in canonical.replace("-", " ").split() if len(part) > 2]


def _namespace(user_id: str) -> str:
    return sha256(user_id.encode("utf-8")).hexdigest()[:12]


def make_node_id(user_id: str, node_type: str, label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", canonical_label(label)).strip("_")
    return f"eb_{_namespace(user_id)}_{node_type.lower()}_{slug}"[:140]


def user_node_id(user_id: str) -> str:
    return f"eb_{_namespace(user_id)}_user"


async def ensure_exam_graph_indexes(db) -> None:
    nodes = db[NODES_COLLECTION]
    rels = db[RELATIONSHIPS_COLLECTION]
    await nodes.create_index(
        [("user_id", 1), ("node_id", 1)],
        unique=True,
        name="uniq_exam_buddy_node",
    )
    await nodes.create_index(
        [("user_id", 1), ("tokens", 1)],
        name="idx_exam_buddy_tokens",
    )
    await rels.create_index(
        [("user_id", 1), ("from_node_id", 1), ("relation", 1), ("to_node_id", 1)],
        unique=True,
        name="uniq_exam_buddy_edge",
    )


async def upsert_fact(
    db,
    user_id: str,
    relation: RelationType | str,
    node_type: NodeType | str,
    label: str,
) -> bool:
    """Insert one user-scoped fact. The same label updates the same node."""
    relation_value = relation.value if isinstance(relation, RelationType) else str(relation)
    type_value = node_type.value if isinstance(node_type, NodeType) else str(node_type)
    if relation_value not in RelationType._value2member_map_:
        return False
    if type_value not in NodeType._value2member_map_ or type_value == NodeType.USER.value:
        return False
    clean = canonical_label(label)
    if len(clean) < MIN_LABEL_LENGTH:
        return False

    now = datetime.now(timezone.utc)
    owner = user_node_id(user_id)
    target = make_node_id(user_id, type_value, clean)
    tokens = label_tokens(clean)

    await db[NODES_COLLECTION].update_one(
        {"user_id": user_id, "node_id": owner},
        {
            "$set": {"node_type": NodeType.USER.value, "label": user_id, "tokens": []},
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    await db[NODES_COLLECTION].update_one(
        {"user_id": user_id, "node_id": target},
        {
            "$set": {
                "node_type": type_value,
                "label": clean,
                "tokens": tokens,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    await db[RELATIONSHIPS_COLLECTION].update_one(
        {
            "user_id": user_id,
            "from_node_id": owner,
            "relation": relation_value,
            "to_node_id": target,
        },
        {"$set": {"updated_at": now}, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return True


async def find_nodes_by_tokens(db, user_id: str, tokens: Iterable[str], limit: int = 12) -> list[dict[str, Any]]:
    wanted = [token for token in tokens if token]
    if not wanted:
        return []
    cursor = db[NODES_COLLECTION].find(
        {
            "user_id": user_id,
            "node_type": {"$ne": NodeType.USER.value},
            "tokens": {"$in": wanted},
        }
    )
    return await cursor.to_list(length=limit)


async def find_nodes_by_type(db, user_id: str, node_type: str, limit: int = 2) -> list[dict[str, Any]]:
    cursor = db[NODES_COLLECTION].find(
        {"user_id": user_id, "node_type": node_type}
    )
    return await cursor.to_list(length=limit)


async def edges_to(db, user_id: str, node_ids: Iterable[str]) -> list[dict[str, Any]]:
    ids = [node_id for node_id in node_ids if node_id]
    if not ids:
        return []
    cursor = db[RELATIONSHIPS_COLLECTION].find(
        {"user_id": user_id, "to_node_id": {"$in": ids}}
    )
    return await cursor.to_list(length=len(ids) + 2)

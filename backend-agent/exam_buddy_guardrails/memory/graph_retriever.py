"""Pull only the learning facts that share words with the current question."""

from __future__ import annotations

from exam_buddy_guardrails.config import MAX_PREFERENCES, MAX_RETRIEVED_MEMORIES
from exam_buddy_guardrails.memory.graph_repository import (
    edges_to,
    find_nodes_by_tokens,
    find_nodes_by_type,
    label_tokens,
)
from exam_buddy_guardrails.models import NodeType, RetrievedMemory

_STOP = {
    "the", "and", "for", "are", "what", "how", "does", "can", "you", "please",
    "explain", "again", "this", "that", "with", "from", "about", "show", "me",
    "solve", "define", "into", "your", "have", "has",
}


def question_tokens(text: str) -> list[str]:
    return [token for token in label_tokens(text) if token not in _STOP]


async def retrieve_memories(db, user_id: str, question: str) -> list[RetrievedMemory]:
    """Targeted lookup. Does not load the student's whole graph."""
    tokens = question_tokens(question)
    topical = await find_nodes_by_tokens(db, user_id, tokens, limit=MAX_RETRIEVED_MEMORIES)
    preferences = await find_nodes_by_type(
        db, user_id, NodeType.LEARNING_PREFERENCE.value, limit=MAX_PREFERENCES
    )
    by_id = {node["node_id"]: node for node in topical}
    for node in preferences:
        by_id.setdefault(node["node_id"], node)
    if not by_id:
        return []

    edges = await edges_to(db, user_id, by_id.keys())
    ranked: list[RetrievedMemory] = []
    for edge in edges:
        node = by_id.get(edge.get("to_node_id"))
        if not node:
            continue
        if node.get("node_type") == NodeType.LEARNING_PREFERENCE.value:
            score = 1
        else:
            node_tokens = set(node.get("tokens") or [])
            if not node_tokens or not node_tokens <= set(tokens):
                continue
            score = 2 + len(node_tokens)
        ranked.append(
            RetrievedMemory(
                relation=str(edge.get("relation") or ""),
                node_type=str(node.get("node_type") or ""),
                label=str(node.get("label") or ""),
                score=score,
            )
        )
    ranked.sort(key=lambda item: item.score, reverse=True)
    unique: list[RetrievedMemory] = []
    seen: set[tuple[str, str]] = set()
    for item in ranked:
        key = (item.relation, item.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= MAX_RETRIEVED_MEMORIES:
            break
    return unique

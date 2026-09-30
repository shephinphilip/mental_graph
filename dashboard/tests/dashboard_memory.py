"""In-memory Mongo stand-in for dashboard tests. Supports the operators the module uses."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _sort_value(value: Any):
    if value is None:
        return (0, "")
    return (1, value)


def _match(doc: dict, query: dict | None) -> bool:
    if not query:
        return True
    if "$and" in query:
        rest = {key: value for key, value in query.items() if key != "$and"}
        return _match(doc, rest) and all(_match(doc, clause) for clause in query["$and"])
    if "$or" in query:
        rest = {key: value for key, value in query.items() if key != "$or"}
        return _match(doc, rest) and any(_match(doc, clause) for clause in query["$or"])
    for key, expected in query.items():
        if key.startswith("$"):
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$exists" in expected and ((key in doc) != bool(expected["$exists"])):
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$gt" in expected and (actual is None or actual <= expected["$gt"]):
                return False
            continue
        if actual != expected:
            return False
    return True


def _project(doc: dict, projection: dict | None) -> dict:
    if not projection:
        return dict(doc)
    include = [key for key, flag in projection.items() if flag and key != "_id"]
    exclude = [key for key, flag in projection.items() if not flag]
    if include:
        out: dict = {}
        for key in include:
            if "." in key:
                head, tail = key.split(".", 1)
                child = doc.get(head)
                if isinstance(child, dict) and tail in child:
                    out.setdefault(head, {})[tail] = child[tail]
            elif key in doc:
                out[key] = doc[key]
        return out
    return {key: value for key, value in doc.items() if key not in exclude}


class Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for field, way in reversed(key):
                self._docs.sort(
                    key=lambda doc, field=field: _sort_value(doc.get(field)),
                    reverse=int(way) < 0,
                )
        else:
            self._docs.sort(
                key=lambda doc: _sort_value(doc.get(key)),
                reverse=int(direction or 1) < 0,
            )
        return self

    def limit(self, count: int):
        self._docs = self._docs[:count]
        return self

    async def to_list(self, length=None):
        if length is None:
            return list(self._docs)
        return list(self._docs[:length])


class Collection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: list = []

    def find(self, query=None, projection=None):
        rows = [_project(doc, projection) for doc in self.docs if _match(doc, query or {})]
        return Cursor(rows)

    async def find_one(self, query=None, projection=None, sort=None):
        rows = [doc for doc in self.docs if _match(doc, query or {})]
        if sort:
            Cursor(rows).sort(sort)
        if not rows:
            return None
        return _project(rows[0], projection)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id="x")

    async def update_one(self, query, update, upsert=False):
        matched = [doc for doc in self.docs if _match(doc, query or {})]
        if matched:
            for key, value in (update.get("$set") or {}).items():
                matched[0][key] = value
            for key in update.get("$unset") or {}:
                matched[0].pop(key, None)
            return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            created = {}
            for key, value in (query or {}).items():
                if not str(key).startswith("$") and not isinstance(value, dict):
                    created[key] = value
            created.update(update.get("$setOnInsert") or {})
            created.update(update.get("$set") or {})
            self.docs.append(created)
            return SimpleNamespace(matched_count=1, modified_count=0, upserted_id="new")
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, kwargs.get("name")))
        return kwargs.get("name")


class MemoryDB(dict):
    def __getitem__(self, name):
        if name not in self:
            super().__setitem__(name, Collection())
        return super().__getitem__(name)

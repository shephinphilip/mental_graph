"""Small in-memory stand-in for the Exam Buddy collections."""

from __future__ import annotations


def _match(doc: dict, query: dict) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_match(doc, part) for part in expected):
                return False
            continue
        value = doc.get(key)
        if isinstance(expected, dict):
            if "$ne" in expected and value == expected["$ne"]:
                return False
            if "$in" in expected:
                wanted = set(expected["$in"])
                if isinstance(value, list):
                    if not wanted.intersection(value):
                        return False
                elif value not in wanted:
                    return False
            continue
        if value != expected:
            return False
    return True


class _Cursor:
    def __init__(self, docs: list[dict]):
        self._docs = docs

    async def to_list(self, length: int = 0):
        if length:
            return [dict(doc) for doc in self._docs[:length]]
        return [dict(doc) for doc in self._docs]


class Collection:
    def __init__(self):
        self.docs: list[dict] = []

    async def create_index(self, *args, **kwargs):
        return None

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if _match(doc, query):
                return dict(doc)
        return None

    def find(self, query):
        return _Cursor([doc for doc in self.docs if _match(doc, query)])

    async def update_one(self, query, update, upsert=False):
        for doc in self.docs:
            if _match(doc, query):
                doc.update(update.get("$set") or {})
                return
        if not upsert:
            return
        created = dict(query)
        created.update(update.get("$setOnInsert") or {})
        created.update(update.get("$set") or {})
        self.docs.append(created)


class MemoryDB(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = Collection()
        return dict.__getitem__(self, name)

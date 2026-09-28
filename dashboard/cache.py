"""In-process cache. Every key is school-scoped by the caller."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Optional

from dashboard.constants import CACHE_MAX_ENTRIES


class TtlCache:
    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES) -> None:
        self._max = max_entries
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        row = self._items.get(key)
        if row is None:
            return None
        expires, value = row
        if expires < time.monotonic():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if ":overview:" in key or key.startswith("dashboard:"):
            if "school" not in key and not _scoped(key):
                raise ValueError("dashboard cache key is missing a school scope")
        self._items[key] = (time.monotonic() + ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


def _scoped(key: str) -> bool:
    parts = key.split("|")
    return len(parts) >= 3 and bool(parts[2])


cache = TtlCache()

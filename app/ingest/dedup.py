"""WP3 — bounded dedup by (source, article id). LRU-evicts past capacity."""
from __future__ import annotations

from collections import OrderedDict


class Dedup:
    def __init__(self, capacity: int = 50_000):
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._cap = capacity

    def add(self, key: str) -> bool:
        """Return True if newly added (not a duplicate)."""
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = None
        if len(self._seen) > self._cap:
            self._seen.popitem(last=False)
        return True

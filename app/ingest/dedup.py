"""WP3 — bounded dedup by (source, article id). LRU-evicts past capacity."""
from __future__ import annotations

from collections import OrderedDict

_MISSING = object()      # distinct from None, which is the stored VALUE of every seen key


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

    def discard(self, key: str) -> bool:
        """ROLL BACK an ``add``. Returns whether the key was present.

        THE KEY MEANS "THIS WAS EMITTED", NOT "WE TRIED TO EMIT THIS". A poller must therefore
        be able to un-say it when the emit FAILS: ``add`` is a check-and-set (there is no
        race-free way to peek first), so the only way to keep the key's meaning honest under a
        TRANSIENT downstream failure is to take it back. Without this, a momentary
        outage (persist_events down, disk full, a DB lock) permanently burns the key and the
        item can NEVER be retried — a silent, unrecoverable evidence loss that the caller's
        counters record as a "drop" while the evidence itself is simply gone.

        BOUND (honest, small): if this key's ``add`` evicted the LRU tail, ``discard`` cannot
        resurrect the evicted key. The worst case is a re-emit of an item older than the whole
        capacity window — a bounded duplicate, never a loss. Losing evidence is the failure we
        refuse; a stale duplicate is not.
        """
        return self._seen.pop(key, _MISSING) is not _MISSING

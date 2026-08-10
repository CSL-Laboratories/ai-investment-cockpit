"""WP3 — Finnhub /news poller: token-bucket (<=60/min) + ``minId`` cursor for incremental
polling. ``fetch(min_id, now) -> list[dict]`` is injected so tests drive it offline.

Hardened (wf_005fd06c): a malformed/absurd ``id`` in untrusted feed content can no longer
crash the batch or poison the cursor — bad ids are skipped, per-item emit is isolated, and the
cursor advances only from EMITTED items, bounded against a poison jump.
"""
from __future__ import annotations

from ingest.dedup import Dedup
from ingest.normalize import to_market_event
from ingest.ratelimit import TokenBucket

MAX_ID_GAP = 100_000_000   # heuristic sanity bound on a single cursor advance (poison guard)


class FinnhubPoller:
    def __init__(self, fetch, bucket=None, dedup=None):
        self._fetch = fetch
        self._bucket = bucket or TokenBucket(rate_per_sec=1.0, capacity=1.0)   # 60/min, starts empty
        self._dedup = dedup or Dedup()
        self._min_id = 0
        # emit_error — on_event raised: TRANSIENT/RETRIABLE, key rolled back, cursor held
        # below the item so the next poll re-fetches it (BUG-069). Same contract as the
        # sibling connectors: A FAILED EMIT IS NOT A DROP.
        self.counters = {"emit_error": 0}

    def poll(self, now: float, on_event) -> int:
        if not self._bucket.allow(now):
            return 0                                          # rate-limited: skip (never exceed)
        emitted, handled_ids, failed_ids = 0, [], []
        for msg in self._fetch(self._min_id, now):
            try:
                mid = int(msg["id"])
            except (KeyError, TypeError, ValueError):
                continue                                      # skip a malformed id; never abort the batch
            related = msg.get("related")
            related = related if isinstance(related, str) else ""
            if self._dedup.add(f"finnhub:{mid}"):
                try:
                    on_event(to_market_event(
                        source="finnhub", article_id=mid, symbols=related,
                        headline=msg.get("headline", ""), summary=msg.get("summary", ""),
                        ts=msg.get("datetime"), raw=msg))
                except Exception:
                    # BUG-069: the key was committed BEFORE the emit (add is check-and-set)
                    # and used to stay committed on failure while later successes advanced
                    # the cursor past the item — the story was burned in dedup, skipped by
                    # the cursor, and never delivered: silent, unrecoverable evidence loss
                    # (the exact class Dedup.discard documents). Roll the key back, count
                    # it, and hold the cursor below the item so the next poll retries.
                    self._dedup.discard(f"finnhub:{mid}")
                    self.counters["emit_error"] += 1
                    failed_ids.append(mid)
                    continue                                  # one bad item never kills the poll
                emitted += 1
                handled_ids.append(mid)
            else:
                handled_ids.append(mid)                       # duplicate: emitted on a prior poll
        if handled_ids:
            candidate = max(handled_ids)
            if failed_ids:
                # never advance past a failed emit — Finnhub's minId is exclusive, so a
                # cursor at/above the failed id would make it unfetchable forever.
                candidate = min(candidate, min(failed_ids) - 1)
            # advance only from HANDLED items, bounded so a poison id can't strand the cursor
            self._min_id = max(self._min_id, min(candidate, self._min_id + MAX_ID_GAP))
        return emitted

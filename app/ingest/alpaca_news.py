"""WP3 — Alpaca Benzinga news consumer (wss://stream.data.alpaca.markets/v1beta1/news).

Subscribes to the whole firehose with the wildcard message (research-supplement §2). The
network is injected as a ``connect() -> iterable-of-frames`` factory (raises on disconnect)
so tests drive it with recorded frames + simulated drops.

Hardened (wf_005fd06c): a bad FRAME (non-str field, missing id) is dropped in its own
try/except and NOT misclassified as a disconnect (so attacker content can't burn the
reconnect budget); the consecutive-failure counter resets on a delivered frame; id-less
frames get a stable synthetic dedup key instead of being silently dropped.
"""
from __future__ import annotations

from ingest.dedup import Dedup
from ingest.normalize import content_hash, to_market_event

SUBSCRIBE = {"action": "subscribe", "news": ["*"]}   # wildcard firehose


def _frame_id(msg: dict):
    return msg.get("id", msg.get("ID"))


def _dedup_key(msg: dict, aid) -> str:
    if aid is not None:
        return f"alpaca:benzinga:{aid}"
    return "alpaca:benzinga:syn:" + content_hash([msg.get("headline"), msg.get("created_at")])


def _to_event(msg: dict) -> dict:
    aid = _frame_id(msg)
    return to_market_event(
        source="alpaca:benzinga",
        article_id=aid if aid is not None else "synthetic",
        symbols=msg.get("symbols", []),
        headline=msg.get("headline", ""),
        summary=msg.get("summary") or msg.get("content", ""),
        ts=msg.get("created_at") or msg.get("updated_at"),
        raw=msg,
    )


class AlpacaNewsConsumer:
    def __init__(self, connect, dedup=None, backoff=None, sleep=None, max_reconnects: int = 5):
        self._connect = connect
        self._dedup = dedup or Dedup()
        self._backoff = backoff or (lambda n: min(30.0, 2.0 ** n))
        self._sleep = sleep or (lambda _s: None)
        self._max_reconnects = max_reconnects
        # emit_error — on_event raised: TRANSIENT/RETRIABLE, key rolled back (BUG-069).
        # frame_error — the FRAME itself was bad (structural), nothing was committed.
        self.counters = {"emit_error": 0, "frame_error": 0}

    def run(self, on_event, max_frames: int | None = None) -> int:
        emitted, seen, attempt = 0, 0, 0
        while True:
            try:
                for msg in self._connect():
                    attempt = 0                            # a delivered frame resets the CONSECUTIVE-failure count
                    seen += 1
                    try:                                   # a bad FRAME is dropped, NOT treated as a disconnect
                        aid = _frame_id(msg)
                        key = _dedup_key(msg, aid)
                        event = _to_event(msg)
                    except Exception:
                        self.counters["frame_error"] += 1  # structural: nothing was committed
                        continue
                    if self._dedup.add(key):
                        try:
                            on_event(event)
                            emitted += 1
                        except Exception:
                            # BUG-069: the per-frame try/except was built to isolate a bad
                            # FRAME but also swallowed a failed EMIT after the key was
                            # committed — a momentary downstream outage permanently burned
                            # every frame it touched. A FAILED EMIT IS NOT A DROP: give
                            # the key back (Dedup.discard) so a redelivered/re-seen frame
                            # can still emit, and count it.
                            self._dedup.discard(key)
                            self.counters["emit_error"] += 1
                    if max_frames is not None and seen >= max_frames:
                        return emitted
                return emitted                             # clean end of stream
            except Exception:                              # real connection/iteration error -> backoff + reconnect
                attempt += 1
                if attempt > self._max_reconnects:
                    raise
                self._sleep(self._backoff(attempt))

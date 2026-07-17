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

from ingest.cramer_signal import detect_cramer_call
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
    headline = msg.get("headline", "")
    summary = msg.get("summary") or msg.get("content", "")
    symbols = msg.get("symbols", [])
    # Inverse Cramer: classify at the ONE true ingest point, BEFORE persist_events binds
    # event_type into the signed EVENT_INGEST_BATCH chain (SEC-11) — so cramer_call:buy/sell
    # is as trustworthy as halt/filing, never a later, unsigned reclassification.
    event_type = "news"
    call = detect_cramer_call(headline, summary, symbols)
    if call is not None:
        _, side = call
        event_type = f"cramer_call:{side}"
    return to_market_event(
        source="alpaca:benzinga",
        article_id=aid if aid is not None else "synthetic",
        symbols=symbols,
        headline=headline,
        summary=summary,
        ts=msg.get("created_at") or msg.get("updated_at"),
        event_type=event_type,
        raw=msg,
    )


class AlpacaNewsConsumer:
    def __init__(self, connect, dedup=None, backoff=None, sleep=None, max_reconnects: int = 5):
        self._connect = connect
        self._dedup = dedup or Dedup()
        self._backoff = backoff or (lambda n: min(30.0, 2.0 ** n))
        self._sleep = sleep or (lambda _s: None)
        self._max_reconnects = max_reconnects

    def run(self, on_event, max_frames: int | None = None) -> int:
        emitted, seen, attempt = 0, 0, 0
        while True:
            try:
                for msg in self._connect():
                    attempt = 0                            # a delivered frame resets the CONSECUTIVE-failure count
                    seen += 1
                    try:                                   # a bad FRAME is dropped, NOT treated as a disconnect
                        aid = _frame_id(msg)
                        if self._dedup.add(_dedup_key(msg, aid)):
                            on_event(_to_event(msg))
                            emitted += 1
                    except Exception:
                        pass
                    if max_frames is not None and seen >= max_frames:
                        return emitted
                return emitted                             # clean end of stream
            except Exception:                              # real connection/iteration error -> backoff + reconnect
                attempt += 1
                if attempt > self._max_reconnects:
                    raise
                self._sleep(self._backoff(attempt))

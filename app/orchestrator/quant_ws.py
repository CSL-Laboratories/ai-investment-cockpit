"""QT-08 — WebSocket state broadcast for the quant engine. Read-only, loopback, key-gated.

SPLIT SIMULATION FROM RENDERING
---------------------------------
The engine computes; anything that displays is a pure SUBSCRIBER. It never runs its own
clock, never holds strategy state, and cannot influence a run. One socket then serves all
three regimes — replay, live paper, and post-hoc playback — because from the subscriber's
side they are indistinguishable: a stream of immutable snapshots.

The alternative, which this exists to avoid, is a UI that runs the simulation inside its own
render loop. Then the display IS the engine: you cannot run headless, cannot replay without
a browser, cannot test the strategy without a DOM, and every rendering change is a change to
the results.

10 Hz, AND WHY NOT FASTER
--------------------------
The engine processes events at ITCH rates; nothing human reads at that speed. Snapshots are
sampled at ~10Hz — fast enough that a book looks live, slow enough that the socket never
becomes the bottleneck the engine waits on. A subscriber that falls behind is DROPPED rather
than buffered: an unbounded queue in front of a slow consumer eventually consumes the memory
of the process doing the actual work, and a stale frame has no value anyway.

THREE EXISTING CONSTRAINTS, ALL PRESERVED
-------------------------------------------
1. EVERY ROUTE REQUIRES THE API KEY. The app-level ``Depends(ApiKeyAuth)`` covers HTTP
   routes; this module checks the handshake header EXPLICITLY rather than relying on
   dependency behaviour for WebSocket routes, because a silent difference between the two
   would be an unauthenticated endpoint on a system whose posture is "even loopback callers
   must hold the key".
2. LOOPBACK ONLY. ``serve`` already calls ``assert_loopback`` at startup; a WebSocket adds no
   new bind and therefore no new inbound surface (security-architecture §6).
3. NO HANDLER IMPORTS ``brokers/``. Stated at the top of ``orchestrator/app.py`` and enforced
   by the Invariant-1 CI gate. This module imports nothing from ``brokers`` or, for that
   matter, from ``quant`` — it broadcasts whatever dictionary it is handed.

READ-ONLY, IN BOTH DIRECTIONS
-------------------------------
The socket never reads a frame from the client. There is no command channel, no parameter
tweak, no start/stop. A subscriber that could send would be a control surface bypassing the
API-key-gated, audited HTTP routes, and it would sit outside every control the cockpit has.
"""
from __future__ import annotations

import asyncio
import hmac
import threading
from dataclasses import dataclass, field
from typing import Callable

# MODULE LEVEL, not inside register_quant_ws. With ``from __future__ import annotations`` the
# handler's parameter annotation is a STRING, and FastAPI resolves it against the module's
# globals — so a function-local import leaves ``WebSocket`` unresolvable and FastAPI falls
# back to treating the parameter as a required query field. The socket then rejects every
# connection with a validation error that looks exactly like an auth failure.
from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .auth import API_KEY_HEADER

BROADCAST_HZ = 10
BROADCAST_INTERVAL_S = 1.0 / BROADCAST_HZ

WS_POLICY_VIOLATION = 1008
"""RFC 6455 close code for a policy violation — what an unauthenticated handshake gets."""


@dataclass(slots=True)
class SnapshotSource:
    """Holds the latest engine snapshot. The engine writes; subscribers read.

    A single slot rather than a queue, deliberately. Subscribers want the CURRENT state, not
    a backlog: replaying a queue of stale books to a client that reconnected is worse than
    useless, because it renders history as though it were now.
    """

    _latest: dict = field(default_factory=dict, repr=False)
    _version: int = field(default=0, repr=False)
    # BUG-081: publish (engine thread) and read (event-loop thread) each touch TWO fields;
    # unsynchronized, a read could pair the new version with the stale snapshot (or miss a
    # fresh one). One lock makes the pair atomic in both directions.
    _lock: object = field(default_factory=threading.Lock, repr=False)

    def publish(self, snapshot: dict) -> int:
        """Replace the current snapshot. Called by the engine; returns the new version."""
        with self._lock:
            self._latest = snapshot
            self._version += 1
            return self._version

    def read(self) -> tuple[int, dict]:
        with self._lock:
            return self._version, self._latest

    @property
    def version(self) -> int:
        with self._lock:
            return self._version


def _key_matches(supplied: str | None, expected: str | None) -> bool:
    """Constant-time comparison, failing closed on an unprovisioned key.

    Mirrors ``auth.ApiKeyAuth`` exactly: no configured key means refuse everyone, never admit
    everyone. Two auth implementations that agree today and drift tomorrow is a real risk, so
    this is deliberately the same three lines rather than a cleverer variant.
    """
    if not expected:
        return False
    if not supplied:
        return False
    return hmac.compare_digest(str(supplied), str(expected))


def register_quant_ws(app, source: SnapshotSource, *,
                      key_provider: Callable[[], str | None],
                      path: str = "/ws/quant",
                      interval_s: float = BROADCAST_INTERVAL_S) -> None:
    """Attach the read-only snapshot socket to a FastAPI app.

    Registered by ``create_app`` only when a source is supplied, so an orchestrator with no
    quant engine attached exposes no extra surface at all — rather than an endpoint that
    accepts connections and then has nothing to say.
    """
    @app.websocket(path)
    async def quant_socket(websocket: WebSocket) -> None:
        # Checked BEFORE accept(): an unauthenticated client is never upgraded, so it cannot
        # observe timing or content differences between "wrong key" and "no data".
        if not _key_matches(websocket.headers.get(API_KEY_HEADER), key_provider()):
            await websocket.close(code=WS_POLICY_VIOLATION)
            return

        await websocket.accept()
        last_sent = -1
        try:
            while True:
                version, snapshot = source.read()
                if version != last_sent and snapshot:
                    await websocket.send_json(snapshot)
                    last_sent = version
                await asyncio.sleep(interval_s)
        except WebSocketDisconnect:
            return
        except (RuntimeError, ConnectionError):
            # A subscriber that died mid-send, or one too slow to keep up. Dropped, not
            # retried and not buffered: an unbounded queue in front of a slow consumer
            # eventually eats the memory of the process doing the real work.
            if websocket.client_state is WebSocketState.CONNECTED:
                await websocket.close(code=WS_POLICY_VIOLATION)
            return


def engine_snapshot(*, symbol: str, book, strategies=(), attribution=None,
                    events: int = 0, fills: int = 0, ts: int = 0,
                    levels: int = 5) -> dict:
    """Build one broadcast frame from live engine objects.

    JSON-NATIVE TYPES ONLY. The frame is also the shape an audit payload would take, and the
    audit chain's canonical encoder has no ``default=`` — it fails closed on anything it
    cannot serialise rather than guessing. Decimals are stringified here rather than floated,
    for the same reason money is Decimal everywhere else in this repo.
    """
    depth = book.snapshot(levels=levels) if hasattr(book, "snapshot") else book
    return {
        "symbol": symbol,
        "ts": ts,
        "seq": depth.seq,
        "book": {
            "bids": [[level.price_ticks, level.qty, level.order_count] for level in depth.bids],
            "asks": [[level.price_ticks, level.qty, level.order_count] for level in depth.asks],
            "spread_ticks": depth.spread_ticks,
            "mid_ticks": depth.mid_ticks,
            "microprice": depth.microprice,
            "imbalance": depth.imbalance,
        },
        "strategies": [s.describe() for s in strategies],
        "attribution": attribution,
        "counters": {"events": events, "fills": fills},
    }

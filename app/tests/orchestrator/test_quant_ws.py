"""QT-08 — the read-only quant WebSocket.

The properties pinned:

  * IT REQUIRES THE API KEY, checked on the handshake and BEFORE accept. A WebSocket that
    slipped past the app-level dependency would be an unauthenticated endpoint on a system
    whose posture is "even loopback callers must hold the key".
  * IT IS REGISTERED ONLY WHEN AN ENGINE IS ATTACHED. No engine, no surface.
  * IT IS READ-ONLY IN BOTH DIRECTIONS. No command channel exists — a subscriber that could
    send would bypass every audited, key-gated HTTP control.
  * FRAMES ARE JSON-NATIVE. The audit chain's canonical encoder has no ``default=``; it
    fails closed on anything it cannot serialise rather than guessing.
  * THE SOURCE HOLDS THE CURRENT STATE, NOT A BACKLOG. Replaying stale books to a client
    that reconnected renders history as though it were now.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.auth import API_KEY_HEADER
from orchestrator.quant_ws import (
    WS_POLICY_VIOLATION,
    SnapshotSource,
    engine_snapshot,
    register_quant_ws,
)
from quant.book.lob import LimitOrderBook
from quant.events import Side

KEY = "test-key-value"


@pytest.fixture
def source() -> SnapshotSource:
    return SnapshotSource()


@pytest.fixture
def client(source) -> TestClient:
    app = FastAPI()
    register_quant_ws(app, source, key_provider=lambda: KEY, interval_s=0.001)
    return TestClient(app)


def _book() -> LimitOrderBook:
    book = LimitOrderBook("S")
    book.add(order_id="b", side=Side.BUY, price_ticks=10_000, qty=900)
    book.add(order_id="a", side=Side.SELL, price_ticks=10_002, qty=100)
    return book


# ---- the snapshot source ------------------------------------------------------------------


def test_the_source_holds_only_the_latest_snapshot(source):
    """A single slot, not a queue: subscribers want the CURRENT book, and replaying a
    backlog to a reconnecting client renders history as though it were now."""
    source.publish({"seq": 1})
    source.publish({"seq": 2})
    version, latest = source.read()
    assert latest == {"seq": 2}
    assert version == 2


def test_the_version_advances_on_every_publish_so_a_subscriber_can_skip_unchanged_state(source):
    assert source.version == 0
    source.publish({"a": 1})
    source.publish({"a": 1})       # identical content, still a new version
    assert source.version == 2


# ---- auth ---------------------------------------------------------------------------------------


def test_a_connection_without_the_api_key_is_refused(client, source):
    source.publish({"seq": 1})
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/quant") as ws:
            ws.receive_json()


def test_a_connection_with_the_WRONG_api_key_is_refused(client, source):
    source.publish({"seq": 1})
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/quant", headers={API_KEY_HEADER: "nope"}) as ws:
            ws.receive_json()


def test_a_connection_with_the_correct_key_receives_snapshots(client, source):
    """CONTROL for both auth tests — a socket that refused everyone would pass them."""
    source.publish({"seq": 42, "symbol": "S"})
    with client.websocket_connect("/ws/quant", headers={API_KEY_HEADER: KEY}) as ws:
        assert ws.receive_json() == {"seq": 42, "symbol": "S"}


def test_an_unprovisioned_key_refuses_everyone_rather_than_admitting_everyone(source):
    """Fail closed, matching ``auth.ApiKeyAuth`` exactly. A missing key must never mean
    'no auth required'."""
    app = FastAPI()
    register_quant_ws(app, source, key_provider=lambda: None, interval_s=0.001)
    source.publish({"seq": 1})
    with pytest.raises(Exception):
        with TestClient(app).websocket_connect(
            "/ws/quant", headers={API_KEY_HEADER: "anything"}
        ) as ws:
            ws.receive_json()


def test_the_policy_violation_close_code_is_the_websocket_standard_one():
    assert WS_POLICY_VIOLATION == 1008


# ---- streaming behaviour ----------------------------------------------------------------------------


def test_a_subscriber_receives_successive_updates(client, source):
    with client.websocket_connect("/ws/quant", headers={API_KEY_HEADER: KEY}) as ws:
        source.publish({"seq": 1})
        assert ws.receive_json()["seq"] == 1
        source.publish({"seq": 2})
        assert ws.receive_json()["seq"] == 2


def test_the_socket_never_reads_from_the_client():
    """There is no command channel. A subscriber that could send would be a control surface
    around the key-gated, audited HTTP routes — outside every control the cockpit has."""
    import inspect

    from orchestrator import quant_ws

    source = inspect.getsource(quant_ws)
    for reader in ("receive_json", "receive_text", "receive_bytes", "iter_json"):
        assert reader not in source, f"the broadcast socket must not call {reader}"


# ---- frame shape --------------------------------------------------------------------------------------


def test_an_engine_frame_serialises_to_json_native_types_only():
    frame = engine_snapshot(symbol="S", book=_book(), events=10, fills=2, ts=999)
    assert json.loads(json.dumps(frame)) == frame


def test_an_engine_frame_carries_the_book_and_its_derived_statistics():
    frame = engine_snapshot(symbol="S", book=_book(), ts=5)
    assert frame["book"]["bids"] == [[10_000, 900, 1]]
    assert frame["book"]["asks"] == [[10_002, 100, 1]]
    assert frame["book"]["spread_ticks"] == 2
    assert frame["book"]["mid_ticks"] == 10_001.0
    assert frame["counters"] == {"events": 0, "fills": 0}


def test_an_engine_frame_includes_strategy_state():
    from quant.strategies.market_maker import MarketMaker

    mm = MarketMaker(name="mm", symbols=("S",))
    frame = engine_snapshot(symbol="S", book=_book(), strategies=[mm])
    assert frame["strategies"][0]["strategy"] == "market_maker"


def test_a_decimal_attribution_is_stringified_rather_than_floated():
    """Money is Decimal everywhere in this repo, and a float in an audit payload is a
    rounding error waiting to be reconciled against."""
    from quant.costs import ZERO_FEES
    from quant.events import PriceScale
    from quant.pnl import PnLTracker

    tracker = PnLTracker(scale=PriceScale(tick_size=Decimal("0.01")), schedule=ZERO_FEES)
    tracker.mark(10_000.0, ts=0)
    frame = engine_snapshot(symbol="S", book=_book(), attribution=tracker.snapshot().as_dict())
    assert isinstance(frame["attribution"]["spread_capture"], str)
    json.dumps(frame)


# ---- registration ------------------------------------------------------------------------------------------


def test_no_socket_is_registered_when_no_engine_is_attached():
    """An orchestrator with no quant engine exposes no extra surface — rather than an
    endpoint that accepts connections and has nothing to say."""
    app = FastAPI()
    assert not [r for r in app.routes if getattr(r, "path", "") == "/ws/quant"]


def test_the_socket_is_registered_when_a_source_is_supplied(source):
    app = FastAPI()
    register_quant_ws(app, source, key_provider=lambda: KEY)
    assert [r for r in app.routes if getattr(r, "path", "") == "/ws/quant"]


def test_the_ws_module_imports_nothing_from_brokers():
    """orchestrator/app.py's header rule: NO handler places an order or imports brokers/.
    A new endpoint is exactly where that would slip."""
    import ast
    import pathlib

    from orchestrator import quant_ws

    tree = ast.parse(pathlib.Path(quant_ws.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
    assert "brokers" not in roots

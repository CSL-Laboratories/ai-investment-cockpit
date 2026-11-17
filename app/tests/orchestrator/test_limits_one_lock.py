"""W2 (cockpit lane): PUT /limits read-classify-append-disarm must be one serialized step.

Synthetic state only: a temporary SQLite audit DB, a test HMAC key, a fixture-owned HALT
path, the handler called directly (no server, no HTTP auth). No network, no broker.
"""
from __future__ import annotations

import importlib
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.limits import DEFAULT_LIMITS, EVENT_LIMITS_CHANGE
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

ROUNDS = 200


@pytest.fixture
def state(tmp_path, monkeypatch):
    ladder_module = importlib.import_module("orchestrator.mode_ladder")
    monkeypatch.setattr(ladder_module, "default_halt_path", lambda: tmp_path / "HALT")
    engine = make_engine(f"sqlite:///{(tmp_path / 'w2.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-w2-test-key",
        settings=Settings(decision_runtime="claude"),
        clock=FrozenClock(datetime(2026, 6, 2, 18, tzinfo=timezone.utc)),
    )


def _put_limits_handler(api, state):
    from orchestrator.auth import ApiKeyAuth

    app = api.create_app(state, auth=ApiKeyAuth(key_provider=lambda: None))
    return next(route.endpoint for route in app.routes
                if route.path == "/limits" and "PUT" in getattr(route, "methods", set()))


def test_w2_two_threads_200_rounds_never_restore_a_looser_limit(state, monkeypatch):
    """Same field, two unconfirmed requests: 100 and 500 against a committed 1000.

    In ANY serial order the committed value ends at 100: 100-then-500 makes 500 a
    loosening that needs confirmation (409); 500-then-100 ends at 100. A final 500 means
    the 500 request was classified against a stale 1000 and committed over the 100,
    loosening the limit with no confirmation and no disarm."""
    from orchestrator import app as api

    original = api._current_limits

    def slow_read(st):
        snapshot = original(st)
        time.sleep(0.001)        # widen read->append; a serialized handler holds its lock here
        return snapshot

    monkeypatch.setattr(api, "_current_limits", slow_read)
    handler = _put_limits_handler(api, state)
    bad = []
    for rnd in range(ROUNDS):
        state.audit.append(EVENT_LIMITS_CHANGE, actor="w2-reset", payload={
            "limits": dict(DEFAULT_LIMITS), "loosened": [], "reason": "round reset"})
        start = threading.Barrier(2, timeout=5)
        out: dict = {}

        def call(tag, value):
            try:
                start.wait()
                out[tag] = handler(api.LimitsUpdate(actor="w2", max_notional=Decimal(value)))
            except BaseException as exc:  # noqa: BLE001 - reported below
                out[tag] = exc

        threads = [threading.Thread(target=call, args=a, daemon=True)
                   for a in (("tight", "100"), ("mid", "500"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert not any(t.is_alive() for t in threads)
        errors = [v for v in out.values() if isinstance(v, BaseException)]
        assert not errors, errors
        final = Decimal(original(state)["max_notional"])
        if final != Decimal("100"):
            bad.append((rnd, str(final)))
    assert not bad, f"{len(bad)}/{ROUNDS} rounds ended looser than 100: {bad[:5]}"


def test_w2_confirmed_loosening_not_in_force_when_disarm_fails(state, monkeypatch):
    """Sibling: the loosening is appended BEFORE the disarm. If the disarm raises, the
    looser limit is in force while auto_veto stays armed. Drop first (as the ceiling
    branch does), so a failed disarm leaves the limit unwritten."""
    from orchestrator import app as api
    from orchestrator.gate import GateResult

    lad = ModeLadder(state)
    lad.set_mode(Mode.paper, actor="w2")
    lad.set_mode(Mode.review_live, actor="w2")
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=GateResult(unlockable=True, target="auto_veto"),
                            operator="w2")
    assert res.armed, res.reasons

    def broken_drop(self, *args, **kwargs):
        raise RuntimeError("synthetic disarm failure")

    monkeypatch.setattr(api.ModeLadder, "drop_to_review_live", broken_drop)
    handler = _put_limits_handler(api, state)
    with pytest.raises(RuntimeError):
        handler(api.LimitsUpdate(actor="w2", max_notional=Decimal("5000"), confirm=True))
    assert api._current_limits(state)["max_notional"] == "1000", (
        "a confirmed loosening is in force although its disarm failed; mode is "
        f"{ModeLadder(state).current().value}")


def test_w2_put_limits_waits_on_the_shared_state_lock(state):
    """Which lock: the ladder's state.lock, the one arm, /mode, the killswitch, the daily
    disarm and the gateway also take. A private lock would still serialize two PUTs (the
    tests above stay green) but would let a loosening read the limits, an arm take
    state.lock, and the loosening append with no disarm. While another thread holds
    state.lock the handler must not complete; once it is released it must."""
    from orchestrator import app as api

    handler = _put_limits_handler(api, state)
    done = threading.Event()
    out: dict = {}

    def call():
        try:
            out["r"] = handler(api.LimitsUpdate(actor="w2", max_notional=Decimal("100")))
        except BaseException as exc:  # noqa: BLE001 - reported below
            out["r"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=call, daemon=True)
    with state.lock:
        worker.start()
        assert not done.wait(0.5), "PUT /limits completed while state.lock was held elsewhere"
    assert done.wait(10), "PUT /limits did not complete after state.lock was released"
    worker.join(10)
    assert not isinstance(out["r"], BaseException), out["r"]
    assert out["r"]["limits"]["max_notional"] == "100"

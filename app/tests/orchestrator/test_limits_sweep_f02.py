"""Sweep regression for finding F02: concurrent tightenings of different limits keep both.

All state is synthetic and all external effects are replaced. No network.
"""
from __future__ import annotations

import importlib
import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def state(tmp_path, monkeypatch):
    # The arming guard must inspect only a fixture-owned, absent sentinel.
    ladder_module = importlib.import_module("orchestrator.mode_ladder")
    monkeypatch.setattr(ladder_module, "default_halt_path", lambda: tmp_path / "HALT")
    engine = make_engine(f"sqlite:///{(tmp_path / 's6.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-s6-test-key",
        settings=Settings(decision_runtime="claude"),
        clock=FrozenClock(datetime(2026, 6, 2, 18, tzinfo=timezone.utc)),
    )


def test_f02_concurrent_tightenings_preserve_both_limits(state, monkeypatch):
    from orchestrator import app as api
    from orchestrator.auth import ApiKeyAuth

    original = api._current_limits
    readers = threading.Barrier(2, timeout=0.3)

    def read_snapshot(st):
        snapshot = original(st)
        try:
            readers.wait()
        except threading.BrokenBarrierError:
            # A fixed handler serializes before reading; the second reader then
            # cannot arrive until the first request has committed.
            pass
        return snapshot

    monkeypatch.setattr(api, "_current_limits", read_snapshot)
    app = api.create_app(state, auth=ApiKeyAuth(key_provider=lambda: None))
    handler = next(route.endpoint for route in app.routes
                   if route.path == "/limits" and "PUT" in getattr(route, "methods", set()))
    errors, results = [], []

    def tighten(field):
        try:
            results.append(handler(api.LimitsUpdate(actor="test", **{field: Decimal("100")})))
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=tighten, args=(field,), daemon=True)
               for field in ("max_notional", "daily_loss_limit")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(3)
    assert not any(worker.is_alive() for worker in workers)
    assert not errors and len(results) == 2
    assert all(result["loosened"] == [] for result in results)
    limits = original(state)
    assert Decimal(limits["max_notional"]) == 100
    assert Decimal(limits["daily_loss_limit"]) == 100

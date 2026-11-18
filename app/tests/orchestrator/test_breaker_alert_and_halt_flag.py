"""Review-thread pins on W3: a breaker trip saved in the same evaluation as an earlier failed
trip still reaches the caller's alert before the failure is re-raised, and a kill's
``halt_set`` records whether the HALT write returned. Synthetic state; no network."""
from __future__ import annotations

import importlib
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers.paper_execution import PaperExecutionService
from brokers.paper_performance import PaperPerformance
from config.settings import Settings
from core.clock import FrozenClock
from orchestrator.breakers import (BRK_CONSECUTIVE_LOSSES, BRK_DAILY_DRAWDOWN, BreakerEngine,
                                   BreakerSignals)
from orchestrator.state import EVENT_KILLSWITCH, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.engine import create_all, make_engine, make_session_factory

NOW = datetime(2026, 6, 2, 18, tzinfo=timezone.utc)
SIGNALS = BreakerSignals(equity_drawdown_today=Decimal("10000"), consecutive_losses=10)


@pytest.fixture
def state(tmp_path, monkeypatch):
    ladder_module = importlib.import_module("orchestrator.mode_ladder")
    monkeypatch.setattr(ladder_module, "default_halt_path", lambda: tmp_path / "HALT")
    engine = make_engine(f"sqlite:///{(tmp_path / 'alert.db').as_posix()}")
    create_all(engine)
    yield OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-alert-key",
        settings=Settings(decision_runtime="claude"), clock=FrozenClock(NOW),
        require_safety_session=False,
    )
    engine.dispose()


def _drawdown_trip_fails(monkeypatch):
    real = BreakerEngine.trip

    def trip(self, name, *args, **kwargs):
        if name == BRK_DAILY_DRAWDOWN:
            raise RuntimeError("synthetic first-trip failure")
        return real(self, name, *args, **kwargs)

    monkeypatch.setattr(BreakerEngine, "trip", trip)


def test_a_trip_saved_beside_a_failed_one_travels_with_the_failure(state, monkeypatch):
    _drawdown_trip_fails(monkeypatch)
    with pytest.raises(RuntimeError) as info:
        BreakerEngine(state).evaluate_and_trip(SIGNALS)
    carried = getattr(info.value, "system_events", None)
    assert carried is not None, "the saved later trip's event was dropped with the raise"
    assert [e.breaker for e in carried] == [BRK_CONSECUTIVE_LOSSES]


def test_the_paper_caller_delivers_the_saved_trip_before_the_reraise(state, monkeypatch):
    deliveries = []
    paper = PaperExecutionService(
        state, broker=None, gateway=None, token_registry=None, quote_fn=lambda _symbol: None,
        deliver=lambda channel, payload: deliveries.append((channel, payload)) or True,
    )
    _drawdown_trip_fails(monkeypatch)
    with pytest.raises(RuntimeError, match="synthetic first-trip failure"):
        paper._refresh_performance(PaperPerformance(
            True, NOW.date().isoformat(), equity_drawdown_today=Decimal("10000"),
            consecutive_losses=10,
        ))
    assert [(c, p["kind"], p["breaker"]) for c, p in deliveries] == [
        ("#system", "breaker_trip", BRK_CONSECUTIVE_LOSSES)]


def test_halt_set_means_the_halt_write_returned(state, tmp_path, monkeypatch):
    """A write that creates the sentinel and then fails keeps ``halt_set`` False and names
    the step; the field is never derived from the file probe (which would read SET)."""
    module = importlib.import_module("orchestrator.killswitch")
    path = tmp_path / "HALT-partial"

    def create_then_fail(p, reason="killswitch"):
        open(p, "w", encoding="utf-8").close()            # what write_text's open() leaves
        raise OSError(28, "synthetic no space left on device")

    monkeypatch.setattr(module, "set_halt", create_then_fail)
    with pytest.raises(OSError):
        module.killswitch(state, actor="test", halt_path=path)
    with state.session_factory() as s:
        row = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_KILLSWITCH)
            .order_by(AuditEntry.seq)
        ).scalars().first()
        payload = dict(row.payload or {})
    assert payload["halt_set"] is False
    assert payload["errors"] == {"halt": "OSError"}

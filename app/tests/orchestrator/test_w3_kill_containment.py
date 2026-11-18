"""W3 regressions: a halt/kill/disarm path must attempt every containment step even when an
earlier one raises, and must surface the failure after it. Synthetic state; no network."""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.breakers import BreakerEngine, BreakerSignals
from orchestrator.gate import GateResult
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_BREAKER_TRIP, EVENT_KILLSWITCH, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from storage.engine import create_all, make_engine, make_session_factory


@pytest.fixture
def state(tmp_path, monkeypatch):
    ladder_module = importlib.import_module("orchestrator.mode_ladder")
    monkeypatch.setattr(ladder_module, "default_halt_path", lambda: tmp_path / "HALT")
    engine = make_engine(f"sqlite:///{(tmp_path / 'w3.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-w3-test-key",
        settings=Settings(decision_runtime="claude"),
        clock=FrozenClock(datetime(2026, 6, 2, 18, tzinfo=timezone.utc)),
    )


def _arm(st) -> ModeLadder:
    ladder = ModeLadder(st)
    ladder.set_mode(Mode.paper, actor="test")
    ladder.set_mode(Mode.review_live, actor="test")
    result = ladder.arm_auto_mode(
        Mode.auto_veto, gate_result=GateResult(True, "auto_veto"), operator="test",
    )
    assert result.armed and ladder.current() is Mode.auto_veto
    return ladder


def _kill_rows(st) -> list[dict]:
    with st.session_factory() as s:
        rows = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_KILLSWITCH)
            .order_by(AuditEntry.seq)
        ).scalars().all()
        return [dict(r.payload or {}) for r in rows]


# ---- the finding, with the audit and the denial (chosen technique) ---------------------
def test_w3_halt_write_failure_completes_kill_audits_and_denies(state, tmp_path, monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    ladder = _arm(state)
    calls = []

    def fail_write(*args, **kwargs):
        raise PermissionError("synthetic unwritable sentinel parent")

    monkeypatch.setattr(module, "set_halt", fail_write)
    with pytest.raises(OSError):
        module.killswitch(
            state, actor="test", halt_path=tmp_path / "unused-HALT",
            cancel_hook=lambda: (calls.append("cancel") or 0),
            disconnect_hook=lambda: calls.append("disconnect"),
        )
    assert calls == ["cancel", "disconnect"]
    assert ladder.current() is Mode.review_live
    rows = _kill_rows(state)
    assert len(rows) == 1
    assert rows[0]["halt_set"] is False and rows[0]["failed_steps"] == ["halt"]
    assert rows[0]["mode_after"] == "review_live"
    assert state.effective_watch_only_reason() is not None   # in-process submit denial
    assert state.audit.verify_chain().ok


# ---- sibling 1: a disarm that raises must not skip the kill audit ----------------------
def test_w3_sibling_disarm_failure_still_audits_the_kill(state, tmp_path, monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    _arm(state)

    def drop(*args, **kwargs):
        raise RuntimeError("synthetic disarm failure")

    monkeypatch.setattr(module, "ModeLadder", lambda st: NS(drop_to_review_live=drop))
    with pytest.raises(RuntimeError):
        module.killswitch(state, actor="test", halt_path=tmp_path / "HALT-k")
    assert (tmp_path / "HALT-k").exists()
    rows = _kill_rows(state)
    assert len(rows) == 1, "a failed disarm skipped the EVENT_KILLSWITCH audit"
    assert rows[0]["failed_steps"] == ["disarm"] and rows[0]["mode_after"] is None
    assert state.effective_watch_only_reason() is not None


# ---- sibling 2: a breaker trip whose record raises must still disarm -------------------
def test_w3_sibling_breaker_trip_record_failure_still_drops(state, monkeypatch):
    ladder = _arm(state)
    real = state.audit.record_state_change

    def record(row, event_type, *args, **kwargs):
        if event_type == EVENT_BREAKER_TRIP:
            raise RuntimeError("synthetic trip-record failure")
        return real(row, event_type, *args, **kwargs)

    monkeypatch.setattr(state.audit, "record_state_change", record)
    with pytest.raises(RuntimeError):
        BreakerEngine(state).trip("daily_drawdown", "synthetic breach", actor="test")
    assert ladder.current() is Mode.review_live, "breaker fired but auto stayed armed"


# ---- sibling 3: one failed trip must not skip the remaining breakers -------------------
def test_w3_sibling_evaluate_and_trip_continues_after_a_failed_trip(state, monkeypatch):
    engine = BreakerEngine(state)
    watch = []

    def trip(*args, **kwargs):
        raise RuntimeError("synthetic trip failure")

    monkeypatch.setattr(engine, "trip", trip)
    monkeypatch.setattr(engine, "needs_watch_only_transition", lambda vendor: True)
    monkeypatch.setattr(engine, "enter_watch_only",
                        lambda vendor, reason, **kw: watch.append(vendor) or NS())
    signals = BreakerSignals(equity_drawdown_today=Decimal("10000"), max_data_age_s=10_000.0)
    with pytest.raises(RuntimeError):
        engine.evaluate_and_trip(signals)
    assert watch == ["market_data"], "a failed drawdown trip skipped the stale-data watch-only"


# ---- policy (owner's point): an unaudited kill also denies in-process ------------------
def test_w3_kill_audit_failure_surfaces_and_denies(state, tmp_path, monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    ladder = _arm(state)
    real = state.audit.append

    def append(event_type, *args, **kwargs):
        if event_type == EVENT_KILLSWITCH:
            raise RuntimeError("synthetic audit failure")
        return real(event_type, *args, **kwargs)

    monkeypatch.setattr(state.audit, "append", append)
    with pytest.raises(RuntimeError):
        module.killswitch(state, actor="test", halt_path=tmp_path / "HALT-a")
    assert (tmp_path / "HALT-a").exists() and ladder.current() is Mode.review_live
    assert state.effective_watch_only_reason() is not None


# ---- answer to the DeepSeek read: pin the contracts the docstrings promise -------------
class _Crisis(BaseException):
    """A BaseException that is not an Exception (the R2-S3 class: CancelledError, SystemExit)."""


@pytest.mark.parametrize("step", ["halt", "disarm", "audit"])
def test_w3_base_exception_at_a_new_step_still_completes_every_step(step, state, tmp_path,
                                                                    monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    ladder = _arm(state)
    calls = []

    def boom(*args, **kwargs):
        raise _Crisis("synthetic")

    if step == "halt":
        monkeypatch.setattr(module, "set_halt", boom)
    elif step == "disarm":
        monkeypatch.setattr(module, "ModeLadder", lambda st: NS(drop_to_review_live=boom))
    else:
        real = state.audit.append

        def append(event_type, *args, **kwargs):
            if event_type == EVENT_KILLSWITCH:
                raise _Crisis("synthetic")
            return real(event_type, *args, **kwargs)

        monkeypatch.setattr(state.audit, "append", append)
    with pytest.raises(_Crisis):
        module.killswitch(
            state, actor="test", halt_path=tmp_path / "HALT-b",
            cancel_hook=lambda: (calls.append("cancel") or 0),
            disconnect_hook=lambda: calls.append("disconnect"),
        )
    assert calls == ["cancel", "disconnect"]
    assert state.effective_watch_only_reason() is not None
    if step != "audit":
        rows = _kill_rows(state)
        assert len(rows) == 1 and rows[0]["failed_steps"] == [step]
    if step != "disarm":
        assert ladder.current() is Mode.review_live
    if step != "halt":
        assert (tmp_path / "HALT-b").exists()


def test_w3_kill_row_holds_type_names_only_and_the_error_names_every_failure(state, tmp_path,
                                                                            monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    _arm(state)
    secret = "postgres://user:pw@host/db /home/user/.ssh/id_rsa"

    def fail_write(*args, **kwargs):
        raise PermissionError(secret)

    def drop(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(module, "set_halt", fail_write)
    monkeypatch.setattr(module, "ModeLadder", lambda st: NS(drop_to_review_live=drop))
    with pytest.raises(PermissionError) as info:
        module.killswitch(state, actor="test", halt_path=tmp_path / "HALT-c")
    rows = _kill_rows(state)
    assert len(rows) == 1
    assert rows[0]["errors"] == {"halt": "PermissionError", "disarm": "RuntimeError"}
    assert "pw@host" not in json.dumps(rows[0]) and ".ssh" not in json.dumps(rows[0])
    notes = getattr(info.value, "__notes__", [])
    assert notes == ["killswitch attempted every step; failed: "
                     "halt (PermissionError), disarm (RuntimeError)"]


def test_w3_failed_denial_install_is_recorded_and_the_kill_still_finishes(state, tmp_path,
                                                                         monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    ladder = _arm(state)

    def fail_write(*args, **kwargs):
        raise PermissionError("synthetic unwritable sentinel parent")

    def deny(reason):
        raise MemoryError("synthetic denial failure")

    monkeypatch.setattr(module, "set_halt", fail_write)
    monkeypatch.setattr(state, "admit_integrity_failure", deny)
    with pytest.raises(PermissionError):
        module.killswitch(state, actor="test", halt_path=tmp_path / "HALT-d")
    assert ladder.current() is Mode.review_live
    rows = _kill_rows(state)
    assert len(rows) == 1
    assert rows[0]["failed_steps"] == ["halt", "deny"]
    assert rows[0]["errors"] == {"halt": "PermissionError", "deny": "MemoryError"}


def test_w3_denial_install_raising_a_base_exception_does_not_abort_the_kill(state, tmp_path,
                                                                           monkeypatch):
    module = importlib.import_module("orchestrator.killswitch")
    ladder = _arm(state)
    calls = []

    def fail_write(*args, **kwargs):
        raise PermissionError("synthetic unwritable sentinel parent")

    def deny(reason):
        raise _Crisis("synthetic denial failure")

    monkeypatch.setattr(module, "set_halt", fail_write)
    monkeypatch.setattr(state, "admit_integrity_failure", deny)
    with pytest.raises(PermissionError):
        module.killswitch(
            state, actor="test", halt_path=tmp_path / "HALT-e",
            cancel_hook=lambda: (calls.append("cancel") or 0),
            disconnect_hook=lambda: calls.append("disconnect"),
        )
    assert calls == ["cancel", "disconnect"]
    assert ladder.current() is Mode.review_live
    rows = _kill_rows(state)
    assert len(rows) == 1
    assert rows[0]["failed_steps"] == ["halt", "deny"]
    assert rows[0]["errors"] == {"halt": "PermissionError", "deny": "_Crisis"}


def test_w3_trip_record_base_exception_still_drops(state, monkeypatch):
    ladder = _arm(state)
    real = state.audit.record_state_change

    def record(row, event_type, *args, **kwargs):
        if event_type == EVENT_BREAKER_TRIP:
            raise _Crisis("synthetic")
        return real(row, event_type, *args, **kwargs)

    monkeypatch.setattr(state.audit, "record_state_change", record)
    with pytest.raises(_Crisis):
        BreakerEngine(state).trip("daily_drawdown", "synthetic breach", actor="test")
    assert ladder.current() is Mode.review_live, "breaker fired but auto stayed armed"


def test_w3_evaluate_and_trip_base_exception_continues_and_names_the_later_failure(state,
                                                                                  monkeypatch):
    engine = BreakerEngine(state)
    watch = []

    def trip(*args, **kwargs):
        raise _Crisis("synthetic")

    monkeypatch.setattr(engine, "trip", trip)
    monkeypatch.setattr(engine, "needs_watch_only_transition", lambda vendor: True)
    monkeypatch.setattr(engine, "enter_watch_only",
                        lambda vendor, reason, **kw: watch.append(vendor) or NS())
    signals = BreakerSignals(equity_drawdown_today=Decimal("10000"), consecutive_losses=1000,
                             max_data_age_s=10_000.0)
    with pytest.raises(_Crisis) as info:
        engine.evaluate_and_trip(signals)
    assert watch == ["market_data"], "a failed trip skipped the stale-data watch-only"
    notes = getattr(info.value, "__notes__", [])
    assert notes == ["breaker consecutive_losses also failed: _Crisis"]

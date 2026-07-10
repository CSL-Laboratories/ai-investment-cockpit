"""W12 — out-of-band, fail-closed kill switch (security-hardening-backlog SEC-04).

Three things this file proves that pre-W12 tests didn't:
  1. the OUT-OF-BAND CLI trigger (``python -m orchestrator.halt set``, exercised here via
     ``orchestrator.halt.main`` — a separate invocation, not a call routed through the
     orchestrator's own request handling) lands the sentinel, and the very next gateway
     submit attempt is refused BEFORE the broker call, even mid-"burst";
  2. ``killswitch``'s ordering is now set-flag -> cancel -> disconnect -> disarm, with the
     disconnect hook observing HALT already set and never blocking containment on failure;
  3. an unreadable sentinel still fails closed (re-asserted here at the OOB-trigger gate,
     alongside the existing ``tests/brokers/test_gateway.py`` coverage).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import orchestrator.halt as halt_mod
from brokers.gateway import OrderGateway
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.halt import default_halt_path, halt_is_set
from orchestrator.killswitch import killswitch
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_KILLSWITCH, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from sqlalchemy import select
from storage.engine import create_all, make_engine, make_session_factory

from tests.brokers.conftest import build_scenario

KEY = b"w12-oob-kill-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def st(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'w12.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(),
                                   clock=FrozenClock(OPEN_UTC))


def _killswitch_audit(st):
    with st.session_factory() as s:
        rows = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_KILLSWITCH)
        ).scalars().all()
    return rows


# ---- (1) the OOB CLI trigger refuses the next submit before the broker ------------------
def test_oob_cli_trigger_mid_burst_refuses_next_submit(st):
    sc = build_scenario(st, mode=Mode.review_live)
    gw: OrderGateway = sc.gateway

    # a "burst" in progress: HALT is not set yet, review would pass.
    review = gw.review_equity_order(sc.order, **sc.review_kwargs())
    assert review.ok

    # a SEPARATE process/terminal drops the OOB trigger — independent of anything the
    # orchestrator's own event loop is doing.
    rc = halt_mod.main(["set", "--reason", "operator oob trigger mid-burst"])
    assert rc == 0
    assert halt_is_set(default_halt_path())

    # the next submit attempt in the same "burst" is refused BEFORE the broker call.
    out = gw.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "halt"


def test_oob_cli_status_and_clear_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "HALT"
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(path))
    assert halt_mod.main(["status"]) == 0
    assert not halt_is_set(path)
    halt_mod.main(["set", "--reason", "test"])
    assert halt_is_set(path)
    halt_mod.main(["clear"])
    assert not halt_is_set(path)


def test_oob_cli_explicit_path_override(tmp_path):
    path = tmp_path / "explicit-HALT"
    halt_mod.main(["set", "--reason", "explicit path", "--path", str(path)])
    assert halt_is_set(path)


# ---- (2) killswitch ordering: set-flag -> cancel -> disconnect -> disarm ---------------
def test_killswitch_calls_cancel_then_disconnect_with_halt_already_set(st):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    ModeLadder(st).set_mode(Mode.review_live, actor="op")
    calls = []

    def cancel_hook():
        calls.append(("cancel", halt_is_set(default_halt_path())))
        return 3

    def disconnect_hook():
        calls.append(("disconnect", halt_is_set(default_halt_path())))

    res = killswitch(st, actor="op", reason="test", cancel_hook=cancel_hook,
                     disconnect_hook=disconnect_hook)

    assert [name for name, _ in calls] == ["cancel", "disconnect"]
    assert all(halt_already_set for _, halt_already_set in calls)
    assert res.cancelled_count == 3
    rows = _killswitch_audit(st)
    assert rows[-1].payload["mcp_disconnected"] is True
    assert rows[-1].payload["mcp_disconnect_error"] is None


def test_killswitch_disconnect_failure_does_not_block_disarm_or_containment(st):
    ModeLadder(st).set_mode(Mode.paper, actor="op")
    ModeLadder(st).set_mode(Mode.review_live, actor="op")

    def boom():
        raise RuntimeError("mcp session already gone")

    res = killswitch(st, actor="op", reason="test", disconnect_hook=boom)
    assert halt_is_set(res.halt_path)                 # containment happened regardless
    assert res.mode_after is Mode.review_live
    rows = _killswitch_audit(st)
    assert rows[-1].payload["mcp_disconnected"] is False
    assert "mcp session already gone" in rows[-1].payload["mcp_disconnect_error"]


def test_killswitch_without_disconnect_hook_is_unaffected():
    """Backward compatibility: every pre-W12 caller omits ``disconnect_hook`` entirely."""
    import inspect

    sig = inspect.signature(killswitch)
    assert sig.parameters["disconnect_hook"].default is None


# ---- (3) unreadable sentinel fails closed at the OOB-trigger gate ----------------------
def test_unreadable_sentinel_fails_closed_on_submit(st, monkeypatch):
    sc = build_scenario(st, mode=Mode.review_live)
    import brokers.gateway as gateway_mod

    def boom(_path):
        raise OSError("permission denied reading HALT")

    monkeypatch.setattr(gateway_mod, "halt_is_set", boom)
    out = sc.gateway.submit(sc.order, **sc.submit_kwargs())
    assert not out.ok and out.stage == "halt"

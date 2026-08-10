"""BUG-059 / BUG-061 — the mode oracle's two unguarded edges.

BUG-059: an EMPTY audit chain fell back to ``settings.mode`` verbatim, and ``COCKPIT_MODE``
is env-configurable with no validator — a fresh (or truncated-to-empty, across a restart)
DB plus ``COCKPIT_MODE=auto_full`` became the live mode oracle with no SEC-20 gate, no
signed arm, no SEC-11 anchor.

BUG-061: ``armed_until`` was stamped into the SIGNED arming payload and then never read
back; disarm rode entirely on the 16:00 APScheduler job (default misfire grace ~1s), so a
process down at close kept an auto mode armed overnight and into the next morning.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator.gate import GateResult
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import OrchestratorState
from storage.engine import create_all, make_engine, make_session_factory

KEY = b"mode-ladder-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)   # 10:00 ET on a trading day


def _st(tmp_path, mode=Mode.read_only):
    engine = make_engine(f"sqlite:///{(tmp_path / 'ladder.db').as_posix()}")
    create_all(engine)
    sf = make_session_factory(engine)
    return OrchestratorState.build(sf, hmac_key=KEY, settings=Settings(mode=mode),
                                   clock=FrozenClock(OPEN_UTC))


@pytest.mark.parametrize("env_mode", [Mode.review_live, Mode.auto_veto, Mode.auto_full])
def test_empty_chain_env_fallback_clamps_order_bearing_rungs(tmp_path, env_mode):
    st = _st(tmp_path, mode=env_mode)
    assert ModeLadder(st).current() is Mode.read_only


@pytest.mark.parametrize("env_mode", [Mode.read_only, Mode.paper])
def test_empty_chain_env_fallback_keeps_orderless_rungs(tmp_path, env_mode):
    st = _st(tmp_path, mode=env_mode)
    assert ModeLadder(st).current() is env_mode


def _arm(st):
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="op")
    lad.set_mode(Mode.review_live, actor="op")
    res = lad.arm_auto_mode(Mode.auto_veto, decision_stage_runtimes=["claude", "claude"],
                            gate_result=GateResult(unlockable=True, target="auto_veto"),
                            operator="op")
    assert res.armed
    return lad, res


def test_armed_until_is_enforced_at_read_time(tmp_path):
    """The missed-cron scenario: the orchestrator restarts after being down at 16:00. The
    signed bound must expire the arm at read time — auto reads as review_live (the rung the
    scheduled disarm lands at), before the bound it reads as armed."""
    st = _st(tmp_path)
    lad, res = _arm(st)
    assert lad.current() is Mode.auto_veto                       # before close: armed
    st.clock._at = res.row.armed_until + timedelta(hours=1)      # 17:00, cron never fired
    assert lad.current() is Mode.review_live
    st.clock._at = res.row.armed_until + timedelta(days=1)       # and it STAYS expired
    assert lad.current() is Mode.review_live


def test_armed_until_not_yet_reached_keeps_the_arm(tmp_path):
    st = _st(tmp_path)
    lad, res = _arm(st)
    st.clock._at = res.row.armed_until - timedelta(minutes=1)
    assert lad.current() is Mode.auto_veto

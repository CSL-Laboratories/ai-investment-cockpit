"""W1 siblings: an auto arm's signed ``armed_until`` bounds what the arm is worth.

The mode oracle (``ModeLadder.current``, BUG-061) reads an auto entry past its signed
``armed_until`` as ``review_live``. These tests hold the two other readers of that entry to the
same bound: the performance gate's time-in-mode count, and the scheduled disarm, which must leave
the same ``mode_change`` row a disarm leaves while the arm is still live. Synthetic state only.
"""
from __future__ import annotations

import importlib
import threading
from datetime import datetime, timedelta, timezone

import pytest

from bridge.anchor import InMemoryAnchorStore, post_head_anchor
from config.settings import Mode, Settings
from core.clock import FrozenClock
from orchestrator import scheduler as sch
from orchestrator.gate import (
    GateResult, _arm_bound, _credited_auto_time, _weeks_in_modes, compute_gate,
)
from orchestrator.killswitch import killswitch
from orchestrator.market_calendar import is_trading_day, to_eastern
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_ADJUDICATION, EVENT_MODE_CHANGE, OrchestratorState
from schemas.models.audit_entry import AuditEntry
from sqlalchemy import select
from storage.engine import create_all, make_engine, make_session_factory

START = datetime(2026, 6, 2, 18, tzinfo=timezone.utc)
WEEK_S = 7 * 24 * 3600


@pytest.fixture
def state(tmp_path, monkeypatch):
    # The arming guard must inspect only a fixture-owned, absent sentinel.
    ladder_module = importlib.import_module("orchestrator.mode_ladder")
    monkeypatch.setattr(ladder_module, "default_halt_path", lambda: tmp_path / "HALT")
    engine = make_engine(f"sqlite:///{(tmp_path / 'w1.db').as_posix()}")
    create_all(engine)
    return OrchestratorState.build(
        make_session_factory(engine), hmac_key=b"synthetic-w1-test-key",
        settings=Settings(decision_runtime="claude"), clock=FrozenClock(START),
    )


def _qualifying_history(st):
    """Five weeks of paper + 30 distinct approvals, then review_live: auto_veto-unlockable."""
    lad = ModeLadder(st)
    lad.set_mode(Mode.paper, actor="test")
    for i in range(30):
        st.audit.append(EVENT_ADJUDICATION, actor="test",
                        payload={"proposal_id": f"synthetic-{i}", "decision": "approve"})
    st.clock._at += timedelta(weeks=5)
    lad.set_mode(Mode.review_live, actor="test")
    return lad


def _arm(lad):
    res = lad.arm_auto_mode(Mode.auto_veto, gate_result=GateResult(True, "auto_veto"),
                            operator="test")
    assert res.armed, res.reasons
    return res.row


def _mode_rows(st):
    with st.session_factory() as s:
        rows = s.execute(select(AuditEntry).where(AuditEntry.event_type == EVENT_MODE_CHANGE)
                         .order_by(AuditEntry.seq)).scalars().all()
        return [dict(r.payload or {}) for r in rows]


def _gate(st, target):
    st.anchor_store = InMemoryAnchorStore()
    post_head_anchor(st, record=False)
    return compute_gate(st, target)


def _auto_full_gate(st):
    return _gate(st, Mode.auto_full)


def _live_weeks(row):
    return (row.armed_until - row.armed_at).total_seconds() / WEEK_S


# ---- the sweep's finding (F01): a two-hour arm earned five weeks ----------------------
def test_f01_expired_auto_arm_does_not_earn_five_weeks(state):
    ladder = ModeLadder(state)
    ladder.set_mode(Mode.paper, actor="test")
    for i in range(30):
        state.audit.append(EVENT_ADJUDICATION, actor="test", payload={
            "proposal_id": f"synthetic-{i}", "decision": "approve",
        })
    state.clock._at += timedelta(weeks=5)
    ladder.set_mode(Mode.review_live, actor="test")
    result = ladder.arm_auto_mode(
        Mode.auto_veto, gate_result=GateResult(True, "auto_veto"), operator="test",
    )
    assert result.armed
    assert result.row.armed_until - result.row.armed_at == timedelta(hours=2)
    state.clock._at = result.row.armed_until
    ladder.daily_disarm()
    state.clock._at += timedelta(weeks=5)
    state.anchor_store = InMemoryAnchorStore()
    post_head_anchor(state, record=False)
    gate = compute_gate(state, Mode.auto_full)
    assert not gate.unlockable
    assert gate.metrics["weeks_in_auto_veto"] <= 2 / 168 + 1e-9


# ---- the scheduled disarm leaves the disarm row ---------------------------------------
def test_live_disarm_row_shape_is_the_reference(state):
    """Reference (green on main): a disarm while the arm is live writes this row."""
    lad = _qualifying_history(state)
    _arm(lad)
    assert lad.daily_disarm() is Mode.review_live
    last = _mode_rows(state)[-1]
    assert {k: last[k] for k in ("kind", "from", "to")} == {
        "kind": "daily_disarm", "from": "auto_veto", "to": "review_live"}
    assert set(last) == {"kind", "from", "to", "reason", "_ts"}


@pytest.mark.parametrize("after_bound", [timedelta(0), timedelta(hours=3)],
                         ids=["cron_at_close", "cron_three_hours_after_bound"])
def test_scheduled_disarm_at_or_after_the_bound_writes_the_disarm_row(state, after_bound):
    lad = _qualifying_history(state)
    row = _arm(lad)
    state.clock._at = row.armed_until + after_bound
    before = len(_mode_rows(state))
    assert sch.run_daily_disarm(state) is Mode.review_live
    rows = _mode_rows(state)
    assert len(rows) == before + 1, "the scheduled disarm wrote no mode_change row"
    last = rows[-1]
    assert {k: last[k] for k in ("kind", "from", "to")} == {
        "kind": "daily_disarm", "from": "auto_veto", "to": "review_live"}
    assert set(last) == {"kind", "from", "to", "reason", "_ts"}
    assert lad.current() is Mode.review_live
    # a second firing is a no-op
    assert sch.run_daily_disarm(state) is Mode.review_live
    assert len(_mode_rows(state)) == before + 1


# ---- the gate counts only the time an arm was live ------------------------------------
def test_missed_disarm_cron_earns_no_auto_credit_past_the_bound(state):
    lad = _qualifying_history(state)
    row = _arm(lad)
    state.clock._at = row.armed_until + timedelta(weeks=5)      # process down at close
    gate = _auto_full_gate(state)
    assert gate.metrics["weeks_in_auto_veto"] <= _live_weeks(row) + 1e-9
    assert not gate.unlockable


def test_killswitch_after_the_bound_earns_no_auto_credit(state, tmp_path):
    lad = _qualifying_history(state)
    row = _arm(lad)
    state.clock._at = row.armed_until + timedelta(hours=1)
    killswitch(state, actor="test", halt_path=tmp_path / "ks-HALT")
    state.clock._at += timedelta(weeks=5)
    gate = _auto_full_gate(state)
    assert gate.metrics["weeks_in_auto_veto"] <= _live_weeks(row) + 1e-9
    assert not gate.unlockable


def test_signed_auto_entry_without_a_bound_earns_no_auto_credit(state):
    """The oracle reads an auto entry with no signed bound as read_only (tamper-shaped); the
    gate must not count it as auto time either."""
    _qualifying_history(state)
    state.audit.append(EVENT_MODE_CHANGE, actor="test",
                       payload={"kind": "arm", "from": "review_live", "to": "auto_veto"})
    state.clock._at += timedelta(weeks=5)
    assert ModeLadder(state).current() is Mode.read_only
    gate = _auto_full_gate(state)
    assert gate.metrics["weeks_in_auto_veto"] == 0
    assert not gate.unlockable


def test_daily_rearm_credit_is_the_sum_of_the_armed_windows(state):
    """Twenty trading days armed 10:00-16:00 ET, disarmed by the scheduler at close: the
    credit is 20 x 6 h, not the calendar span (OWNER'S POINT: the strict reading)."""
    lad = _qualifying_history(state)
    state.clock._at = datetime(2026, 7, 7, 14, tzinfo=timezone.utc)   # Tue 10:00 ET
    armed_s, days = 0.0, 0
    while days < 20:
        if is_trading_day(to_eastern(state.clock.now()).date()):
            row = _arm(lad)
            armed_s += (row.armed_until - row.armed_at).total_seconds()
            state.clock._at = row.armed_until
            sch.run_daily_disarm(state)
            days += 1
        state.clock._at = (to_eastern(state.clock.now()).replace(hour=10, minute=0)
                           + timedelta(days=1)).astimezone(timezone.utc)
    gate = _auto_full_gate(state)
    assert armed_s == 20 * 6 * 3600
    assert gate.metrics["weeks_in_auto_veto"] == pytest.approx(armed_s / WEEK_S)
    assert not gate.unlockable


# ---- the new write is idempotent under two concurrent firings --------------------------
def test_two_concurrent_scheduled_disarms_write_one_row(state):
    lad = _qualifying_history(state)
    state.clock._at = datetime(2026, 7, 7, 14, tzinfo=timezone.utc)
    for _ in range(200):
        row = _arm(lad)
        state.clock._at = row.armed_until
        before = len(_mode_rows(state))
        go = threading.Barrier(2)
        errors = []

        def fire():
            try:
                go.wait(5)
                sch.run_daily_disarm(state)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=fire) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert not errors
        assert len(_mode_rows(state)) == before + 1
        assert lad.current() is Mode.review_live
        state.clock._at = row.armed_until + timedelta(hours=18)


# ---- OWNER'S POINT 2, pinned as built: the tail of an expired arm is review_live time -----
def test_op2_tail_of_an_expired_arm_counts_toward_the_lower_gate(state):
    """Three weeks of paper, a two-hour arm, then a week with no disarm row (the process was
    down). The mode oracle reads the tail as review_live, so it counts toward ``weeks_in_lower``:
    4.00 weeks, enough to unlock auto_veto (main reads 3.00 and stays locked, because main counts
    that week as auto). The stricter alternative, crediting the tail to no bucket, is a change in
    ``_credited_auto_time`` and would turn this test red."""
    lad = ModeLadder(state)
    lad.set_mode(Mode.paper, actor="test")
    for i in range(30):
        state.audit.append(EVENT_ADJUDICATION, actor="test",
                           payload={"proposal_id": f"synthetic-{i}", "decision": "approve"})
    state.clock._at += timedelta(weeks=3)
    lad.set_mode(Mode.review_live, actor="test")
    row = _arm(lad)
    assert row.armed_until - row.armed_at == timedelta(hours=2)
    state.clock._at = row.armed_until + timedelta(weeks=1)
    assert lad.current() is Mode.review_live
    gate = _gate(state, Mode.auto_veto)
    assert gate.metrics["weeks_in_lower"] == pytest.approx(4.0)
    assert gate.metrics["weeks_in_auto_veto"] == pytest.approx(2 / 168)
    assert gate.unlockable, gate.reasons


# ---- the bound as the oracle reads it -------------------------------------------------
def test_arm_bound_without_a_zone_is_no_bound():
    """The oracle reads a naive ``armed_until`` as read_only, so the gate gives it no credit."""
    assert _arm_bound({"armed_until": "2026-06-02T20:00:00"}) is None
    assert _arm_bound({"armed_until": "2026-06-02T20:00:00+00:00"}) == datetime(2026, 6, 2, 20)
    assert _arm_bound({"armed_until": "2026-06-02T16:00:00-04:00"}) == datetime(2026, 6, 2, 20)


@pytest.mark.parametrize("until", ["not-a-date", 12345, ["2026-06-02T20:00:00+00:00"], True, ""],
                         ids=["text", "number", "list", "bool", "empty"])
def test_arm_bound_that_does_not_parse_is_no_bound(until):
    assert _arm_bound({"armed_until": until}) is None
    assert _arm_bound({}) is None


# ---- the credited times are never negative ---------------------------------------------
def test_credited_auto_time_is_never_negative():
    t0 = datetime(2026, 6, 2, 14)
    hour = timedelta(hours=1)
    # bound before the start: nothing is auto, everything is the tail
    assert _credited_auto_time(t0, t0 + 2 * hour, t0 - hour) == (0.0, 7200.0)
    # bound after the end: everything is auto, the tail is empty
    assert _credited_auto_time(t0, t0 + 2 * hour, t0 + 5 * hour) == (7200.0, 0.0)
    # the clock went back inside the entry
    assert _credited_auto_time(t0, t0 - hour, t0 + hour) == (0.0, 0.0)
    assert _credited_auto_time(t0, t0 + 2 * hour, None) == (0.0, 0.0)


def test_weeks_in_modes_clamps_a_clock_that_went_back():
    t0 = datetime(2026, 6, 2, 14)
    hour = timedelta(hours=1)
    events = [(t0 + hour, "paper", None), (t0, "paper", None)]      # second row is EARLIER
    assert _weeks_in_modes(events, t0 + 2 * hour, {"paper"}) == pytest.approx(2 * 3600 / WEEK_S)


# ---- the two guards of the scheduled disarm's read of the signed row --------------------
def test_latest_signed_mode_ignores_a_row_that_fails_its_signature(state):
    lad = _qualifying_history(state)
    assert lad._latest_signed_mode() is Mode.review_live
    state.audit.append(EVENT_MODE_CHANGE, actor="test",
                       payload={"kind": "arm", "from": "review_live", "to": "paper"})
    assert lad._latest_signed_mode() is Mode.paper
    with state.session_factory() as s:
        entry = lad._latest_entry(s)
        entry.payload = {**entry.payload, "to": "auto_veto"}          # edited after signing
        s.commit()
    assert lad._latest_signed_mode() is None


def test_latest_signed_mode_ignores_a_mode_name_it_does_not_know(state):
    lad = _qualifying_history(state)
    state.audit.append(EVENT_MODE_CHANGE, actor="test",
                       payload={"kind": "arm", "from": "review_live", "to": "not_a_mode"})
    assert lad._latest_signed_mode() is None

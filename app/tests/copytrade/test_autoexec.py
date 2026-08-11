"""RW-17 §1 — the PAPER auto-execution lane, proved end to end.

The claim this file exists to execute rather than assert: **a disclosure-shaped, risk-approved
order reaches a SUBMITTED paper order with ZERO human calls, through the ONE order path, with
every gateway gate satisfied rather than bypassed.** ``bridge/approval.record_decision`` is
monkeypatched to explode in the end-to-end test, so "no human was involved" is enforced by the
test harness, not by reading the code.

Everything else here is the other half of the same claim: every way this lane can REFUSE is
named, counted, and reachable. Fail-closed controls that are never exercised are decoration.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers.gateway import OrderGateway
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from config.settings import Mode, Settings
from copytrade import autoexec as autoexec_mod
from copytrade.autoexec import (
    ALL_RESOLUTION_REASONS,
    AUTOEXEC_ACTOR,
    NOTIFY_CANCELLED,
    NOTIFY_QUEUED,
    NOTIFY_REFUSED,
    NOTIFY_SUBMITTED,
    REASON_ALREADY_RESOLVED,
    REASON_APPROVAL_REPLAY,
    REASON_BREAKER_TRIPPED,
    REASON_CANCELLED,
    REASON_CHAIN_UNVERIFIED,
    REASON_EXECUTED,
    REASON_HALT,
    REASON_MODE_NOT_PAPER,
    REASON_NOT_PAPER_TRANSPORT,
    REASON_UNDELIVERED,
    REASON_WATCH_ONLY,
    PaperAutoExecutor,
)
from copytrade.budget import (
    AUTOEXEC_LANE,
    RAIL_DAILY_NOTIONAL,
    RAIL_OPEN_POSITIONS,
    RAIL_SYMBOL_NOTIONAL,
    RAIL_TRADES_PER_DAY,
    AutoExecBudget,
)
from core.clock import FrozenClock
from core.ids import new_id
from orchestrator.breakers import BRK_DAILY_LOSS, BreakerEngine
from orchestrator.halt import set_halt
from orchestrator.mode_ladder import ModeLadder
from orchestrator.state import EVENT_ADJUDICATION, OrchestratorState
from schemas.models.approval_record import ApprovalRecord
from schemas.models.audit_entry import AuditEntry
from schemas.models.order import Order
from schemas.models.risk_decision import RiskDecision
from storage.engine import create_all, make_engine, make_session_factory
from workflows.copytrade_orders import COPYTRADE_RUNTIME

KEY = b"rw17-autoexec-key"
OPEN_UTC = datetime(2026, 7, 7, 14, 0, tzinfo=timezone.utc)
OPERATOR = "123456789012345678"          # a well-formed Discord snowflake
WINDOW = 300.0


# ---- harness ----------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_halt(tmp_path, monkeypatch):
    """Per-test HALT sentinel: a HALT set by one test must never leak into another, and the
    lane must never touch the real machine-wide sentinel."""
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    return tmp_path / "cockpit-HALT"


@pytest.fixture(autouse=True)
def _operator_allowlist(monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)


@pytest.fixture
def clock():
    return FrozenClock(OPEN_UTC)


@pytest.fixture
def st(tmp_path, clock):
    engine = make_engine(f"sqlite:///{(tmp_path / 'rw17-autoexec.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(make_session_factory(engine), hmac_key=KEY,
                                    settings=Settings(), clock=clock)
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    return state


class Notifier:
    """The operator-surface seam. Records every event and reports whether delivery was
    CONFIRMED — the return value IS the confirmation (see ``PaperAutoExecutor._deliver``)."""

    def __init__(self, confirm: bool = True) -> None:
        self.confirm = confirm
        self.events: list[tuple[str, dict]] = []

    def __call__(self, event: str, payload: dict) -> bool:
        self.events.append((event, payload))
        return self.confirm

    def kinds(self) -> list[str]:
        return [e for e, _p in self.events]

    def last(self, kind: str) -> dict:
        return [p for e, p in self.events if e == kind][-1]


class FakeMonotonic:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RecordingBroker:
    """Stands in for the Alpaca PAPER transport. The gateway keeps it private; this records
    that — and exactly when — a broker call happened, which is the property under test."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def submit_order(self, request: dict) -> dict:
        self.calls.append(dict(request))
        return {"broker_order_id": f"paper-{len(self.calls)}", "status": "accepted"}


def approved_recheck(_order):
    return lambda _positions: {"decision": "approved", "failed_checks": []}


def make_order(st, *, symbol="AAPL", side="buy", qty="1", limit_price="50", notional="50",
               proposal_id=None):
    """One risk-approved ``Order`` row in ``proposed``, the shape ``run_copy_cycle`` leaves
    behind (RW-16 §6 ends at exactly this row and goes no further)."""
    proposal_id = proposal_id or new_id("prop")
    now = st.clock.now()
    rd = RiskDecision(
        id=new_id("rd"), proposal_id=proposal_id, decision="approved", checks={},
        failed_checks=[], warnings=[], max_allowed_notional=Decimal("5000"),
        human_required=False, evaluated_at=now, created_at=now,
    )
    st.audit.record_state_change(rd, "risk_decision", actor=COPYTRADE_RUNTIME,
                                 payload={"decision": "approved"})
    canon = canonical_order_tuple(symbol=symbol, side=side, qty=qty, order_type="limit",
                                  limit_price=limit_price, tif="day")
    order = create_order(st, proposal_id=proposal_id, risk_decision_id=rd.id, account="paper",
                         symbol=symbol, side=side, qty=qty, order_type="limit",
                         limit_price=limit_price, tif="day", notional=notional,
                         order_tuple_hash=order_tuple_hash(canon), actor=COPYTRADE_RUNTIME)
    return order, rd, proposal_id


def build(st, *, notifier=None, budget=None, mono=None, broker=None, window=WINDOW):
    broker = broker or RecordingBroker()
    # execution_scope='paper' — the gateway's own transport split (_ORDER_AUTHORITY_BY_SCOPE)
    # admits ONLY Mode.paper on this transport. Live capital is unreachable by construction.
    gateway = OrderGateway(broker, st=st, execution_scope="paper")
    executor = PaperAutoExecutor(
        st, gateway, window_seconds=window, notify=notifier or Notifier(),
        budget=budget, monotonic=mono or FakeMonotonic(),
        risk_recheck_factory=approved_recheck,
    )
    return executor, gateway, broker


def order_state(st, order_id: str) -> str:
    with st.session_factory() as s:
        return s.get(Order, order_id).state


def lane_approvals(st) -> list[ApprovalRecord]:
    with st.session_factory() as s:
        rows = s.execute(select(ApprovalRecord)).scalars().all()
        for r in rows:
            s.expunge(r)
        return rows


# ---- 1. the whole point: zero-touch paper execution --------------------------------------
def test_zero_touch_paper_execution_reaches_the_broker_with_no_human_call(st, monkeypatch):
    """A disclosure-shaped order -> queued+notified -> window elapses -> SUBMITTED, and not one
    human function was called on the way."""
    def _no_humans(*_a, **_k):                      # pragma: no cover - must never run
        raise AssertionError("the auto lane must not route through the human approval gateway")

    monkeypatch.setattr("bridge.approval.record_decision", _no_humans)

    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, proposal_id = make_order(st)

    ticket = executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    # Queued and CONFIRMED delivered — the window clock only starts here (SEC-23).
    assert notifier.kinds() == [NOTIFY_QUEUED]
    assert ticket.delivered is True
    queued = notifier.last(NOTIFY_QUEUED)
    assert queued["symbol"] == "AAPL" and queued["cancel_command"].endswith(proposal_id)
    # Nothing has reached the broker while the window is open.
    assert executor.resolve_due() == [] and broker.calls == []

    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert resolution.executed is True and resolution.reason == REASON_EXECUTED
    assert resolution.broker_order_id == "paper-1"
    assert len(broker.calls) == 1
    assert broker.calls[0]["symbol"] == "AAPL"
    assert order_state(st, order.id) == "submitted"
    assert NOTIFY_SUBMITTED in notifier.kinds()
    assert executor.counts[REASON_EXECUTED] == 1


def test_the_auto_approval_is_a_real_persisted_record_the_gateway_reloads(st):
    """Gate (e) re-loads the ``ApprovalRecord`` BY ID and demands an HMAC-signed adjudication
    whose ``refs.row_id`` is that id. An appended event alone would refuse every order, so
    this pins the actual mechanism rather than the outcome."""
    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()
    assert resolution.executed is True

    [approval] = lane_approvals(st)
    assert approval.id == resolution.approval_id
    assert approval.user_decision == "approve"
    assert approval.order_tuple_hash == order.order_tuple_hash
    # The approval was CONSUMED by the submit — single-use, as for a human approval.
    assert approval.single_use_consumed is True

    with st.session_factory() as s:
        entries = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
        ).scalars().all()
        covering = [e for e in entries
                    if (e.refs or {}).get("row_id") == approval.id
                    and (e.payload or {}).get("decision") == "approve"]
        assert len(covering) == 1
        entry = covering[0]
        assert st.audit.verify_entry(entry) is True
        assert entry.payload["proposal_id"] == proposal_id
        assert entry.payload["risk_decision_id"] == rd.id
        assert entry.payload["order_tuple_hash"] == order.order_tuple_hash


def test_the_adjudicating_actor_is_a_robot_and_is_not_an_authorized_operator(st):
    """The audit log must say a ROBOT decided this. Recording the operator's id would be a
    lie in the one record that exists to say who is responsible."""
    from bridge.operators import is_authorized

    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    executor.resolve_due()

    [approval] = lane_approvals(st)
    assert approval.actor_user_id == AUTOEXEC_ACTOR
    assert is_authorized(AUTOEXEC_ACTOR, st.settings) is False


# ---- 2. paper only, by construction ------------------------------------------------------
def test_a_ticket_scheduled_in_paper_and_resolved_in_review_live_does_not_execute(st):
    """The mode is re-read at the FIRING edge, not at schedule time. A ticket queued under
    paper is VOID the moment the ladder moves, and is never re-queued at the new rung."""
    mono = FakeMonotonic()
    notifier = Notifier()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    ModeLadder(st).set_mode(Mode.review_live, actor="op")
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert resolution.executed is False
    assert resolution.reason == REASON_MODE_NOT_PAPER
    assert broker.calls == []
    assert lane_approvals(st) == []
    assert order_state(st, order.id) == "proposed"
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_MODE_NOT_PAPER
    assert executor.counts[REASON_MODE_NOT_PAPER] == 1


def test_the_lane_uses_the_paper_scoped_transport_so_live_is_unreachable(st):
    """Belt to the mode re-check's braces: even if the mode check were removed, the paper
    gateway's own authority set admits no live rung."""
    from brokers.gateway import _ORDER_AUTHORITY_BY_SCOPE

    assert _ORDER_AUTHORITY_BY_SCOPE["paper"] == frozenset({"paper"})
    _executor, gateway, _broker = build(st)
    assert gateway._allowed_modes == frozenset({"paper"})


def test_a_live_scoped_gateway_is_refused_before_anything_is_minted(st):
    """A lane that adjudicates without a human may exist ONLY in front of the paper transport.
    Refused by name, and BEFORE an approval or a capability token exists — discovering it later
    would strand an unconsumed approval on the append-only chain."""
    mono, broker = FakeMonotonic(), RecordingBroker()
    executor = PaperAutoExecutor(
        st, OrderGateway(broker, st=st, execution_scope="live"), window_seconds=WINDOW,
        notify=Notifier(), monotonic=mono, risk_recheck_factory=approved_recheck)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert (resolution.executed, resolution.reason) == (False, REASON_NOT_PAPER_TRANSPORT)
    assert broker.calls == [] and lane_approvals(st) == []


def test_the_brokers_seam_itself_refuses_a_non_paper_gateway(st):
    """The belt behind that braces: even a caller that skipped the resolve-time check cannot
    place an auto-adjudicated order through a live transport."""
    from brokers.auto_paper_submit import auto_paper_place
    from brokers.capability import CapabilityError

    gateway = OrderGateway(RecordingBroker(), st=st, execution_scope="live")
    with pytest.raises(CapabilityError):
        auto_paper_place(gateway, object(), approval=None, risk_decision=None, ladder=None,
                         breaker_engine=None, risk_recheck=None, positions=[],
                         token_nonce="n", producing_runtime=COPYTRADE_RUNTIME)


# ---- 3. the environmental controls, each named -------------------------------------------
def test_halt_set_mid_window_refuses_by_name(st, _isolate_halt):
    mono = FakeMonotonic()
    notifier = Notifier()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    set_halt(_isolate_halt, reason="operator pulled the cord mid-window")
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert (resolution.executed, resolution.reason) == (False, REASON_HALT)
    assert broker.calls == [] and lane_approvals(st) == []
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_HALT
    assert executor.counts[REASON_HALT] == 1


def test_watch_only_mid_window_refuses_by_name(st):
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    BreakerEngine(st).enter_watch_only("anthropic", "usage window exhausted")
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert (resolution.executed, resolution.reason) == (False, REASON_WATCH_ONLY)
    assert broker.calls == [] and lane_approvals(st) == []
    assert executor.counts[REASON_WATCH_ONLY] == 1


def test_a_tripped_breaker_mid_window_refuses_by_name(st):
    """In ``paper`` a breaker trip does NOT move the ladder (``drop_to_review_live`` is a no-op
    below review_live), so the mode re-check cannot see it. Reading the signed trip/clear
    ledger is what makes a tripped breaker actually stop this lane."""
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    BreakerEngine(st).trip(BRK_DAILY_LOSS, "daily realized loss limit breached")
    assert ModeLadder(st).current() is Mode.paper       # the ladder genuinely did not move
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert (resolution.executed, resolution.reason) == (False, REASON_BREAKER_TRIPPED)
    assert BRK_DAILY_LOSS in resolution.detail
    assert broker.calls == [] and lane_approvals(st) == []


def test_a_cleared_breaker_stops_blocking_the_lane(st):
    """The trip/clear ledger is read latest-wins, so a cleared breaker does not wedge the lane
    forever — otherwise the only recovery would be a database edit."""
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    engine = BreakerEngine(st)
    engine.trip(BRK_DAILY_LOSS, "breached")
    engine.clear(BRK_DAILY_LOSS, "operator reviewed and cleared")
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()
    assert resolution.executed is True and len(broker.calls) == 1


def test_an_unverifiable_audit_chain_refuses(st):
    """A keyless tail truncation. Nothing may be executed or SPENT against a ledger that does
    not verify — and the refusal names the tamper rather than the mode read it also breaks."""
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    with st.session_factory() as s, s.begin():
        head = s.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)).scalar_one()
        s.delete(head)
    assert st.audit.verify_chain().ok is False

    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert (resolution.executed, resolution.reason) == (False, REASON_CHAIN_UNVERIFIED)
    assert broker.calls == [] and lane_approvals(st) == []


def test_an_undelivered_notification_blocks_execution_and_is_retried(st):
    """SEC-23: silence is not consent. An unnotified auto-trade is the one thing the operator
    explicitly did not ask for, so the window never even starts."""
    mono = FakeMonotonic()
    notifier = Notifier(confirm=False)
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, proposal_id = make_order(st)
    ticket = executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    assert ticket.delivered is False
    mono.advance(WINDOW * 10)
    [resolution] = executor.resolve_due()
    assert (resolution.executed, resolution.reason) == (False, REASON_UNDELIVERED)
    assert broker.calls == []
    assert notifier.kinds().count(NOTIFY_QUEUED) == 2        # retried on the tick

    # The surface recovers. The cancel window starts NOW, not retroactively: the operator gets
    # the full window from the moment they could actually SEE the trade, so an outage cannot
    # silently consume their only lever.
    notifier.confirm = True
    # The retry BACKS OFF (adversarial round): a failing surface is not re-attempted on every
    # 60s tick, so the clock has to reach the next attempt watermark first. Until then the
    # ticket is still undelivered and still refuses to fire.
    assert executor.resolve_due()[0].reason == REASON_UNDELIVERED
    assert broker.calls == []
    mono.advance(PaperAutoExecutor._NOTICE_BACKOFF_SECONDS[-1])
    assert executor.resolve_due() == []                      # delivery confirmed at T=0
    assert broker.calls == []
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True


def test_a_notifier_that_raises_is_an_undelivered_notification(st):
    def boom(_event, _payload):
        raise RuntimeError("the operator surface is down")

    mono = FakeMonotonic()
    executor, _gw, broker = build(st, notifier=boom, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].reason == REASON_UNDELIVERED
    assert broker.calls == []


def test_an_unwired_notifier_never_trades(st):
    """A lane with no operator surface is a lane that executes silently. Refuse."""
    mono = FakeMonotonic()
    executor = PaperAutoExecutor(st, OrderGateway(RecordingBroker(), st=st,
                                                  execution_scope="paper"),
                                 window_seconds=WINDOW, notify=None, monotonic=mono,
                                 risk_recheck_factory=approved_recheck)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].reason == REASON_UNDELIVERED


def test_an_unwired_risk_recheck_refuses_rather_than_inventing_an_approval(st):
    """Gate (d) re-runs risk immediately before the broker call. This lane cannot compute that
    verdict itself, so the DEFAULT must refuse."""
    mono = FakeMonotonic()
    broker = RecordingBroker()
    executor = PaperAutoExecutor(st, OrderGateway(broker, st=st, execution_scope="paper"),
                                 window_seconds=WINDOW, notify=Notifier(), monotonic=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()
    assert resolution.executed is False
    assert "autoexec_risk_recheck_not_wired" in resolution.detail
    assert broker.calls == []


def test_a_backward_monotonic_clock_refuses_and_trips_the_injection_breaker(st):
    """A rewound time source trying to make the window look elapsed early (SEC-23)."""
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    executor._read_monotonic()                    # establish the watermark at the high value
    mono.t -= 10_000                              # ... then rewind under it

    [resolution] = executor.resolve_due()
    assert resolution.executed is False
    assert resolution.reason == autoexec_mod.REASON_CLOCK_BACKWARDS
    assert broker.calls == []
    assert autoexec_mod._tripped_breakers(st)     # the injection breaker fired


# ---- 4. the budget rails, each exhausting into a named, counted refusal -------------------
def _execute_one(st, executor, mono, **order_kw):
    order, rd, proposal_id = make_order(st, **order_kw)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    resolutions = [r for r in executor.resolve_due() if r.proposal_id == proposal_id]
    return resolutions[0]


@pytest.mark.parametrize("budget, second_order, rail", [
    (AutoExecBudget(max_trades_per_day=1), {"symbol": "MSFT"}, RAIL_TRADES_PER_DAY),
    (AutoExecBudget(max_daily_notional=Decimal("60")), {"symbol": "MSFT"},
     RAIL_DAILY_NOTIONAL),
    (AutoExecBudget(max_notional_per_symbol_per_day=Decimal("60")), {"symbol": "AAPL"},
     RAIL_SYMBOL_NOTIONAL),
    (AutoExecBudget(max_open_positions=1), {"symbol": "MSFT"}, RAIL_OPEN_POSITIONS),
])
def test_each_budget_rail_exhausts_into_a_named_counted_refusal(st, budget, second_order, rail):
    """Every rail is exhausted by a REAL first trade whose spend is re-derived from the signed
    chain — not by a hand-built state object. Exhaustion is a NAMED, NOTIFIED refusal, never a
    silent stop."""
    mono, notifier = FakeMonotonic(), Notifier()
    executor, _gw, broker = build(st, notifier=notifier, budget=budget, mono=mono)

    assert _execute_one(st, executor, mono, symbol="AAPL").executed is True
    second = _execute_one(st, executor, mono, **second_order)

    assert second.executed is False
    assert second.reason == rail
    assert second.detail                                  # says WHICH rail and by how much
    assert executor.counts[rail] == 1
    assert len(broker.calls) == 1                         # the second never reached the broker
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == rail


def test_the_budget_survives_a_restart_of_the_executor(st):
    """The rails are the operator's replacement, so "restart the process" must not be an
    authorization mechanism. A brand-new executor re-derives the spend from the chain."""
    budget = AutoExecBudget(max_trades_per_day=1)
    mono = FakeMonotonic()
    executor, gateway, broker = build(st, budget=budget, mono=mono)
    assert _execute_one(st, executor, mono, symbol="AAPL").executed is True

    fresh = PaperAutoExecutor(st, gateway, window_seconds=WINDOW, notify=Notifier(),
                              budget=budget, monotonic=mono,
                              risk_recheck_factory=approved_recheck)
    assert _execute_one(st, fresh, mono, symbol="MSFT").reason == RAIL_TRADES_PER_DAY
    assert len(broker.calls) == 1


# ---- 5. the operator's only lever --------------------------------------------------------
def test_a_cancelled_ticket_never_executes_and_cancel_is_idempotent(st):
    mono, notifier = FakeMonotonic(), Notifier()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    first = executor.cancel(proposal_id, actor_user_id=OPERATOR)
    assert (first.cancelled, first.reason) == (True, REASON_CANCELLED)
    second = executor.cancel(proposal_id, actor_user_id=OPERATOR)
    assert second.cancelled is True and second.reason == "already_cancelled"

    mono.advance(WINDOW + 1)
    assert [r.reason for r in executor.resolve_due()] == []      # terminal; nothing to resolve
    assert broker.calls == []
    assert lane_approvals(st) == []
    assert order_state(st, order.id) == "proposed"
    assert notifier.kinds().count(NOTIFY_CANCELLED) == 1         # no second effect

    # The cancel is on the signed chain, as a VETO — the operator never approves anything.
    with st.session_factory() as s:
        vetoes = [e for e in s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_ADJUDICATION)
        ).scalars().all() if (e.payload or {}).get("decision") == "veto"]
    assert len(vetoes) == 1
    assert vetoes[0].payload["lane"] == AUTOEXEC_LANE


def test_a_foreign_actor_cannot_cancel_and_the_attempt_is_audited(st):
    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)

    outcome = executor.cancel(proposal_id, actor_user_id="987654321098765432")
    assert (outcome.cancelled, outcome.reason) == (False, "unauthorized")
    with st.session_factory() as s:
        unauthorized = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == "unauthorized_actor")
        ).scalars().all()
    assert len(unauthorized) == 1

    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True    # the trade still proceeds


def test_cancelling_an_already_executed_trade_is_refused_not_silently_accepted(st):
    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True

    outcome = executor.cancel(proposal_id, actor_user_id=OPERATOR)
    assert (outcome.cancelled, outcome.reason) == (False, "already_executed")


# ---- 6. single use ------------------------------------------------------------------------
def test_a_resolved_ticket_cannot_be_replayed_into_a_second_order(st):
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True

    for _ in range(3):
        mono.advance(WINDOW + 1)
        assert executor.resolve_due() == []              # terminal tickets are not re-examined
    assert len(broker.calls) == 1
    assert len(lane_approvals(st)) == 1


def test_a_re_armed_ticket_cannot_mint_a_second_auto_approval(st):
    """The in-memory ticket is the braces; the persisted, UNIQUE ``interaction_id`` is the
    belt. Forcing the ticket live again (what a process restart or a tampered ticket map would
    do) must still not produce a second approval or a second broker call."""
    mono = FakeMonotonic()
    executor, _gw, broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    ticket = executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True

    ticket.resolved = False
    ticket.executed = False
    mono.advance(WINDOW + 1)
    [resolution] = executor.resolve_due()

    assert (resolution.executed, resolution.reason) == (False, REASON_APPROVAL_REPLAY)
    assert len(broker.calls) == 1
    assert len(lane_approvals(st)) == 1


def test_resolving_a_terminal_ticket_reports_already_resolved(st):
    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    ticket = executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    executor.cancel(proposal_id, actor_user_id=OPERATOR)
    ticket.resolved = False                              # forced back into the live set
    ticket.cancelled = True
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].reason == REASON_CANCELLED
    ticket.resolved = False
    assert executor.resolve_due()[0].reason in (REASON_CANCELLED, REASON_ALREADY_RESOLVED)


# ---- 7. hygiene ---------------------------------------------------------------------------
def test_every_reason_this_lane_can_report_is_in_the_closed_vocabulary(st):
    """``ALL_RESOLUTION_REASONS`` is what the operator surface aggregates on: an outcome
    outside it would be a silent drop."""
    assert len(set(ALL_RESOLUTION_REASONS)) == len(ALL_RESOLUTION_REASONS)
    executor, _gw, _broker = build(st)
    assert set(executor.counts) == set(ALL_RESOLUTION_REASONS)


def test_the_lane_never_widens_the_ladder(st):
    """Nothing in this package may make live capital reachable. The lane executes and the
    ladder is exactly where it was."""
    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True
    assert ModeLadder(st).current() is Mode.paper


# ---- 8. the orchestrator lock is never held across an operator-surface POST -----------------
def test_a_refusal_notification_is_delivered_with_st_lock_RELEASED(st):
    """RW-17 adversarial A6, proved by a probe that runs on a DIFFERENT thread from the deliver.

    ``st.lock`` is the single orchestrator lock: it guards ``OrderGateway.review/submit``,
    ``ModeLadder.set_mode``, the breaker engine and every audit write. Delivery is a network
    POST with its own retry budget, so a refusal delivered while the lock is held stalls the
    HUMAN order path and the killswitch's mode drop for as long as the surface hangs — and with
    the resolve tick at ``max_instances=1``, every following tick is dropped too. The SUBMITTED
    path always deferred to the outbox; the refusal paths did not.

    WHY THE PROBE CANNOT LIVE INSIDE THE NOTIFIER. ``st.lock`` is an RLock, so a probe that
    calls ``acquire`` from within the notifier callback is asking the lock's OWNER whether it is
    free, and re-entrancy answers "yes" whether or not the bug is present. The probe has to run
    on a thread that does not hold the lock. So: the worker blocks inside the delivery, and THIS
    thread — which owns nothing — is the one that tries to take the lock. It can only succeed if
    the delivery genuinely happens outside the locked section.
    """
    entered_delivery = threading.Event()
    may_return = threading.Event()

    def blocking_notifier(event, _payload):
        if event == NOTIFY_REFUSED:
            entered_delivery.set()
            may_return.wait(timeout=10)          # stands in for a hung webhook
        return True

    mono = FakeMonotonic()
    executor, _gw, broker = build(
        st, notifier=blocking_notifier, mono=mono,
        # every rail at 1 => the ticket refuses at a budget rail, i.e. on the refusal path.
        budget=AutoExecBudget(max_daily_notional=Decimal("1"), max_trades_per_day=1,
                              max_open_positions=1,
                              max_notional_per_symbol_per_day=Decimal("1")),
    )
    order, rd, proposal_id = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=proposal_id)
    mono.advance(WINDOW + 1)

    worker = threading.Thread(target=executor.resolve_due, daemon=True)
    worker.start()
    assert entered_delivery.wait(timeout=10), "the refusal was never delivered at all"

    acquired = st.lock.acquire(timeout=5)        # the whole assertion, taken from outside
    if acquired:
        st.lock.release()
    may_return.set()
    worker.join(timeout=10)

    assert acquired is True, "st.lock was HELD across the operator-surface delivery"
    assert not worker.is_alive()
    assert broker.calls == [], "a refused ticket must never reach the broker"

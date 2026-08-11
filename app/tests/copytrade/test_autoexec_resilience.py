"""RW-17 adversarial-round regressions — the LIVENESS half of the auto-execution lane.

``test_autoexec.py`` proves the lane's AUTHORITY is right: a real persisted approval, every
gateway gate satisfied, paper-only by construction. This file proves the other half, which the
adversarial round found broken in six places at once, and every one of those defects had the
same shape:

    **an outcome that reaches nobody.**

A broker 503 that killed the whole tick and burned the day's budget for zero trades. A hung
webhook holding the orchestrator lock. A five-minute HALT that stranded a filing forever. A
restart that vaporised a trade the operator had been promised. An executed trade whose
notification failed and was never retried, counted, or mentioned again. Tickets that could never
expire, retrying a dead surface every minute.

Each test below FAILS on the code as it was. They are the reason "the lane did nothing today" is
a statement the operator can now trust.
"""
from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from brokers.gateway import OrderGateway
from brokers.order_state_machine import create_order
from brokers.order_tuple import canonical_order_tuple, order_tuple_hash
from copytrade import autoexec as autoexec_mod
from copytrade.autoexec import (
    EVENT_AUTOEXEC_TICKET,
    NOTIFY_QUEUED,
    NOTIFY_REFUSED,
    NOTIFY_SUBMITTED,
    REASON_ABANDONED_RESTART,
    REASON_APPROVAL_REPLAY,
    REASON_EXECUTED,
    REASON_HALT,
    REASON_NOTIFY_UNDELIVERED,
    REASON_PENDING_EXPOSURE,
    REASON_POSITIONS_UNAVAILABLE,
    REASON_QUEUE_FULL,
    REASON_RESOLVE_FAULT,
    REASON_SUPERSEDED,
    REASON_TICKET_EXPIRED,
    TRANSIENT_REASONS,
    PaperAutoExecutor,
)
from copytrade.budget import RAIL_TRADES_PER_DAY, AutoExecBudget, budget_state
from copytrade.digest import digest_rows
from core.ids import new_id
from orchestrator.halt import clear_halt, set_halt
from schemas.models.audit_entry import AuditEntry
from workflows.copytrade_orders import COPYTRADE_RUNTIME

from tests.copytrade.test_autoexec import (
    KEY,
    OPEN_UTC,
    OPERATOR,
    WINDOW,
    FakeMonotonic,
    Notifier,
    RecordingBroker,
    approved_recheck,
    lane_approvals,
    make_order,
    order_state,
)

BIG = AutoExecBudget(max_daily_notional=Decimal("100000"), max_trades_per_day=100,
                     max_open_positions=100,
                     max_notional_per_symbol_per_day=Decimal("100000"))


# ---- harness ---------------------------------------------------------------------------
# The plain HELPERS are imported from ``test_autoexec`` (one harness, one place); the FIXTURES
# are declared here rather than imported, because importing a fixture and then naming it as a
# test parameter shadows the import in every signature.
@pytest.fixture(autouse=True)
def _isolate_halt(tmp_path, monkeypatch):
    """Per-test HALT sentinel — a HALT set by one test must never leak into another, and the
    lane must never touch the real machine-wide sentinel."""
    monkeypatch.setenv("COCKPIT_HALT_PATH", str(tmp_path / "cockpit-HALT"))
    return tmp_path / "cockpit-HALT"


@pytest.fixture(autouse=True)
def _operator_allowlist(monkeypatch):
    monkeypatch.setenv("COCKPIT_OPERATOR_IDS", OPERATOR)


@pytest.fixture
def st(tmp_path):
    from config.settings import Mode, Settings
    from core.clock import FrozenClock
    from orchestrator.mode_ladder import ModeLadder
    from orchestrator.state import OrchestratorState
    from storage.engine import create_all, make_engine, make_session_factory

    engine = make_engine(f"sqlite:///{(tmp_path / 'rw17-resilience.db').as_posix()}")
    create_all(engine)
    state = OrchestratorState.build(make_session_factory(engine), hmac_key=KEY,
                                    settings=Settings(), clock=FrozenClock(OPEN_UTC))
    ModeLadder(state).set_mode(Mode.paper, actor="op")
    return state


def build(st, *, notifier=None, mono=None, positions_fn=None, budget=None, broker=None,
          gateway=None, window=WINDOW, ticket_max_age=None, recheck=None):
    broker = broker or RecordingBroker()
    gateway = gateway or OrderGateway(broker, st=st, execution_scope="paper")
    executor = PaperAutoExecutor(
        st, gateway, window_seconds=window, notify=notifier or Notifier(),
        monotonic=mono or FakeMonotonic(), budget=budget if budget is not None else BIG,
        risk_recheck_factory=recheck or approved_recheck,
        positions_fn=positions_fn or (lambda: []),
        ticket_max_age_seconds=ticket_max_age,
    )
    return executor, gateway, broker


def ticket_events(st) -> list[dict]:
    with st.session_factory() as s:
        rows = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_AUTOEXEC_TICKET)
            .order_by(AuditEntry.seq)
        ).scalars().all()
        return [dict(r.payload or {}) for r in rows]


# =============================================================================================
# 1. per-ticket isolation, and a data fault that costs NOTHING
# =============================================================================================
def test_a_positions_fault_refuses_by_name_and_spends_no_budget(st):
    """``PaperExecutionService.positions_snapshot`` documents that it RAISES on a broker fault.

    It used to be evaluated as a call ARGUMENT to ``auto_paper_place`` — i.e. after the approval
    was already on the signed chain. One 503 therefore killed the tick, left an unconsumed
    approval that made the NEXT tick refuse the filing forever as ``approval_replay``, and
    consumed a slot of ``max_trades_per_day`` plus its dollars for a trade that never reached a
    broker. Three of those exhausted the shipped daily budget with zero trades and zero
    messages, which is indistinguishable from "Congress filed nothing".
    """
    notifier, mono = Notifier(), FakeMonotonic()
    boom = {"raise": True}

    def positions_fn():
        if boom["raise"]:
            raise ConnectionError("alpaca /v2/positions 503")
        return []

    executor, _gw, broker = build(st, notifier=notifier, mono=mono, positions_fn=positions_fn)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)

    [res] = executor.resolve_due()                    # no longer raises out of the tick

    assert (res.executed, res.reason) == (False, REASON_POSITIONS_UNAVAILABLE)
    assert lane_approvals(st) == [], "nothing may be minted before the data is in hand"
    assert budget_state(st, day=st.clock.now().date()).trades == 0, "no budget was spent"
    assert broker.calls == []
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_POSITIONS_UNAVAILABLE

    # TRANSIENT: the ticket is still live, so the trade happens once the vendor recovers.
    assert REASON_POSITIONS_UNAVAILABLE in TRANSIENT_REASONS
    assert [t.proposal_id for t in executor.pending()] == [pid]
    boom["raise"] = False
    [res] = executor.resolve_due()
    assert res.executed is True and len(broker.calls) == 1


def test_one_faulting_ticket_never_abandons_the_tickets_behind_it(st):
    """``scheduler.run_autoexec_resolve`` documents this as ``resolve_due``'s own contract —
    "one ticket that cannot be adjudicated must be settled and counted without abandoning the
    tickets behind it" — and the control did not exist. Ticket B was simply never looked at: no
    resolution, no counter, no notification, and the operator's only interface said nothing."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    a_order, a_rd, a_pid = make_order(st, symbol="AAPL")
    b_order, b_rd, b_pid = make_order(st, symbol="MSFT")
    executor.schedule(order=a_order, risk_decision=a_rd, proposal_id=a_pid)
    executor.schedule(order=b_order, risk_decision=b_rd, proposal_id=b_pid)
    mono.advance(WINDOW + 1)

    # A fault with no seam of its own: the ladder lookup itself explodes for ticket A only.
    real_load = executor._load_order

    def exploding_load(order_id):
        if order_id == a_order.id:
            raise RuntimeError("sqlite is briefly unavailable")
        return real_load(order_id)

    executor._load_order = exploding_load

    resolutions = {r.proposal_id: r for r in executor.resolve_due()}

    assert resolutions[a_pid].reason == REASON_RESOLVE_FAULT
    assert executor.counts[REASON_RESOLVE_FAULT] == 1
    assert notifier.last(NOTIFY_REFUSED)["proposal_id"] in (a_pid, b_pid)
    # ...and B, behind it in the list, still traded.
    assert resolutions[b_pid].executed is True
    assert len(broker.calls) == 1 and broker.calls[0]["symbol"] == "MSFT"


def test_a_resolve_fault_is_transient_so_the_filing_is_not_stranded(st):
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)

    real_load = executor._load_order
    executor._load_order = lambda _oid: (_ for _ in ()).throw(RuntimeError("blip"))
    assert executor.resolve_due()[0].reason == REASON_RESOLVE_FAULT

    executor._load_order = real_load
    assert executor.resolve_due()[0].executed is True
    assert len(broker.calls) == 1


# =============================================================================================
# 2. nothing is delivered while the orchestrator lock is held
# =============================================================================================
def test_a_hung_notification_surface_does_not_hold_the_orchestrator_lock(st):
    """``st.lock`` guards ``OrderGateway.review/submit``, ``ModeLadder.set_mode`` and the
    breaker engine. The refusal paths used to POST to the operator surface while holding it, so
    a hung Discord webhook stalled the human order path, the mode ladder and the killswitch's
    mode drop — and, because the resolve tick runs every minute with ``max_instances=1``, every
    following tick was dropped too. The SUBMITTED path had always got this right; the refusals
    now do as well."""
    released = threading.Event()
    seen = {"lock_free_during_post": None}

    def hanging_notifier(event, payload):
        if event == NOTIFY_REFUSED:
            acquired = st.lock.acquire(blocking=False)
            seen["lock_free_during_post"] = acquired
            if acquired:
                st.lock.release()
            released.set()
        return True

    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, notifier=hanging_notifier, mono=mono,
                                   budget=AutoExecBudget(
                                       max_daily_notional=Decimal("1"),
                                       max_trades_per_day=1, max_open_positions=1,
                                       max_notional_per_symbol_per_day=Decimal("1")))
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)

    # A different THREAD is what proves it: an RLock is re-entrant for its owner, so testing
    # from this thread would pass even with the bug present.
    worker = threading.Thread(target=lambda: executor.resolve_due())
    worker.start()
    worker.join(timeout=10)
    assert not worker.is_alive()
    assert released.is_set(), "the refusal notification must actually have been attempted"
    assert seen["lock_free_during_post"] is True, "st.lock was HELD across the delivery"


# =============================================================================================
# 3. a live ticket is never silently replaced, and the queue is bounded
# =============================================================================================
def test_rescheduling_a_live_proposal_settles_the_displaced_ticket(st):
    """``schedule`` used to assign ``self._tickets[proposal_id]`` unconditionally. The first,
    already-notified ticket was dropped: never resolved, never counted, never notified — and the
    ``/cancel <proposal_id>`` the operator was holding silently re-targeted a DIFFERENT order,
    so one tap would veto a trade they had only been told about in a second message."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, _broker = build(st, notifier=notifier, mono=mono)
    order_a, rd_a, pid = make_order(st, symbol="AAPL", limit_price="50", notional="50")
    first = executor.schedule(order=order_a, risk_decision=rd_a, proposal_id=pid)

    # the same proposal, re-priced, queued again before the first window closes
    order_b = create_order(
        st, proposal_id=pid, risk_decision_id=rd_a.id, account="paper", symbol="AAPL",
        side="buy", qty="1", order_type="limit", limit_price="90", tif="day", notional="90",
        order_tuple_hash=order_tuple_hash(canonical_order_tuple(
            symbol="AAPL", side="buy", qty="1", order_type="limit", limit_price="90",
            tif="day")),
        actor=COPYTRADE_RUNTIME)
    second = executor.schedule(order=order_b, risk_decision=rd_a, proposal_id=pid)

    assert first.resolved is True, "the displaced ticket must be SETTLED, never dropped"
    assert executor.counts[REASON_SUPERSEDED] == 1
    refusals = [p for e, p in notifier.events if e == NOTIFY_REFUSED]
    assert refusals and refusals[-1]["refused_by"] == REASON_SUPERSEDED
    assert [t.proposal_id for t in executor.pending()] == [pid]
    assert second.resolved is False


def test_the_live_ticket_queue_is_capped_and_the_refusal_is_named(st):
    """Nothing upstream bounds how many orders one cycle may produce, so a bad parse cycle used
    to queue hundreds of tickets and — on a degraded surface — retry hundreds of notifications
    every single minute, forever, against a rate limit the storm itself trips."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, _broker = build(st, notifier=notifier, mono=mono)
    cap = PaperAutoExecutor._MAX_LIVE_TICKETS

    for _ in range(cap):
        o, rd, pid = make_order(st)
        executor.schedule(order=o, risk_decision=rd, proposal_id=pid)
    assert len(executor.pending()) == cap

    o, rd, pid = make_order(st)
    overflow = executor.schedule(order=o, risk_decision=rd, proposal_id=pid)

    assert overflow.resolved is True
    assert executor.counts[REASON_QUEUE_FULL] == 1
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_QUEUE_FULL
    assert len(executor.pending()) == cap, "the cap holds"


# =============================================================================================
# 4. tickets expire; transient refusals do not strand the filing
# =============================================================================================
def test_a_ticket_that_can_never_resolve_expires_by_name(st, _isolate_halt):
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono, ticket_max_age=1800)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)

    set_halt(_isolate_halt)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].reason == REASON_HALT
    assert executor.pending(), "a HALT is transient — the ticket stays alive for the retry"

    mono.advance(1800)
    [res] = executor.resolve_due()

    assert res.reason == REASON_TICKET_EXPIRED
    assert executor.pending() == []
    assert broker.calls == []
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_TICKET_EXPIRED
    clear_halt(_isolate_halt)


def test_a_HALT_does_not_strand_the_filing_forever(st, _isolate_halt):
    """``workflows/copytrade_orders._consuming`` says it verbatim: "A HALT window is transient by
    design; a retry is the correct response to it." A terminal refusal killed the ticket while
    the ``Order`` row kept the disclosure marked consumed, so the filing was never re-proposed
    and the trade could never happen — a five-minute killswitch permanently losing a filing."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)

    set_halt(_isolate_halt)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].reason == REASON_HALT
    assert broker.calls == []

    clear_halt(_isolate_halt)
    [res] = executor.resolve_due()

    assert res.executed is True and len(broker.calls) == 1
    assert order_state(st, order.id) == "submitted"


def test_a_per_day_budget_rail_is_transient_not_terminal(st):
    """``max_trades_per_day`` is a PER-DAY rail. Refusing terminally on it meant any cycle that
    produced more orders than the daily cap stranded the excess in ``proposed`` forever — with
    the shipped default of 3, any PTR batch with four approved orders."""
    notifier, mono = Notifier(), FakeMonotonic()
    budget = AutoExecBudget(max_daily_notional=Decimal("10000"), max_trades_per_day=1,
                            max_open_positions=10,
                            max_notional_per_symbol_per_day=Decimal("10000"))
    executor, _gw, broker = build(st, notifier=notifier, mono=mono, budget=budget)
    a = make_order(st, symbol="AAPL")
    b = make_order(st, symbol="MSFT")
    executor.schedule(order=a[0], risk_decision=a[1], proposal_id=a[2])
    executor.schedule(order=b[0], risk_decision=b[1], proposal_id=b[2])
    mono.advance(WINDOW + 1)

    res = {r.proposal_id: r for r in executor.resolve_due()}
    assert res[a[2]].executed is True
    assert res[b[2]].reason == RAIL_TRADES_PER_DAY
    assert [t.proposal_id for t in executor.pending()] == [b[2]], "tomorrow's rail is fresh"

    # TOMORROW: the same, still-live ticket is re-evaluated against a fresh day's rail. The
    # resolve service passes the wall-clock instant in, so this is exactly what the 00:01 tick
    # does — no re-proposal, no human, no lost filing.
    tomorrow = st.clock.now() + timedelta(days=1)
    [res_b] = executor.resolve_due(now=tomorrow)
    assert res_b.executed is True
    assert len(broker.calls) == 2


def test_a_transient_refusal_is_notified_ONCE_per_reason_not_every_tick(st, _isolate_halt):
    """The resolve tick is every 60s. Re-sending the identical refusal every minute for the
    duration of a HALT is how the one channel the operator has left becomes noise."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, _broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    set_halt(_isolate_halt)
    mono.advance(WINDOW + 1)

    for _ in range(5):
        executor.resolve_due()

    assert notifier.kinds().count(NOTIFY_REFUSED) == 1
    assert executor.counts[REASON_HALT] == 5, "every refusal is still COUNTED"
    clear_halt(_isolate_halt)


# =============================================================================================
# 5. the advertised cancel window is never shorter than advertised
# =============================================================================================
def test_the_window_does_not_fire_AT_the_advertised_deadline(st):
    """``mono < delivered + window`` fired at exactly T+window_seconds, so a cancel landing on
    the stated deadline lost. The queued message prints ``window_seconds`` as the operator's
    allowance; the real window must never be shorter than the advertised one."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)

    mono.advance(WINDOW)                                  # exactly the advertised deadline
    assert executor.resolve_due() == [], "the ticket must not fire AT the stated deadline"
    assert broker.calls == []
    # ...so the operator acting on the deadline they were shown still WINS.
    assert executor.cancel(pid, actor_user_id=OPERATOR).cancelled is True

    mono.advance(1)
    assert executor.resolve_due() == []                    # settled: cancelled, never executed
    assert broker.calls == []
    assert executor._tickets[pid].executed is False


# =============================================================================================
# 6. every terminal notification is retried and counted
# =============================================================================================
def test_an_undelivered_SUBMITTED_notice_is_counted_and_retried(st):
    """The return value of the submitted/refused/cancelled deliveries was DISCARDED. A surface
    outage at fire time therefore produced a trade that reached the broker with the operator
    never told it happened, no counter, and no retry — the single worst outcome this lane can
    produce."""
    class Flaky(Notifier):
        def __init__(self):
            super().__init__()
            self.allow = {NOTIFY_QUEUED}

        def __call__(self, event, payload):
            self.events.append((event, payload))
            return event in self.allow

    notifier, mono = Flaky(), FakeMonotonic()
    executor, _gw, broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)

    [res] = executor.resolve_due()
    assert res.executed is True and len(broker.calls) == 1
    assert executor.counts[REASON_NOTIFY_UNDELIVERED] == 1, "the failure is NAMED and counted"

    # the surface recovers; the notice is re-sent without a second trade
    notifier.allow.add(NOTIFY_SUBMITTED)
    mono.advance(PaperAutoExecutor._NOTICE_BACKOFF_SECONDS[-1])
    executor.resolve_due()

    assert notifier.kinds().count(NOTIFY_SUBMITTED) == 2
    assert len(broker.calls) == 1, "a re-notified trade is never a second trade"


def test_an_abandoned_notice_is_bounded_and_loud(st):
    def never(_event, _payload):
        return _event == NOTIFY_QUEUED

    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, notifier=never, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)
    executor.resolve_due()

    for _ in range(PaperAutoExecutor._MAX_NOTICE_ATTEMPTS + 2):
        mono.advance(PaperAutoExecutor._NOTICE_BACKOFF_SECONDS[-1])
        executor.resolve_due()

    ticket = executor._tickets[pid]
    assert ticket.pending_notices == [], "the retry converges rather than storming forever"
    assert executor.counts[REASON_NOTIFY_UNDELIVERED] == PaperAutoExecutor._MAX_NOTICE_ATTEMPTS


# =============================================================================================
# 7. a restart never vaporises a promised trade
# =============================================================================================
def test_a_restart_inside_the_cancel_window_settles_the_promise(st):
    """Tickets lived only in ``self._tickets``. A restart between the queued notification and the
    window edge left the operator holding a message reading "this WILL execute unless you cancel
    it" — and, by the design's own rule, no reason to act. Nothing executed, nothing was refused,
    no outcome was ever delivered, and ``/cancel`` answered "unknown ticket"."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, gateway, broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    assert notifier.kinds() == [NOTIFY_QUEUED]
    assert any(p["state"] == "queued" for p in ticket_events(st)), "the promise is on the chain"

    reborn = PaperAutoExecutor(
        st, gateway, window_seconds=WINDOW, notify=notifier, monotonic=mono, budget=BIG,
        risk_recheck_factory=approved_recheck, positions_fn=lambda: [])
    [res] = reborn.recover_orphans()

    assert (res.executed, res.reason) == (False, REASON_ABANDONED_RESTART)
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_ABANDONED_RESTART
    assert broker.calls == [] and lane_approvals(st) == []
    assert reborn.counts[REASON_ABANDONED_RESTART] == 1
    # idempotent: a THIRD process finds nothing left to settle
    third = PaperAutoExecutor(
        st, gateway, window_seconds=WINDOW, notify=notifier, monotonic=mono, budget=BIG,
        risk_recheck_factory=approved_recheck, positions_fn=lambda: [])
    assert third.recover_orphans() == []


def test_a_ticket_that_already_settled_is_not_recovered_twice(st):
    notifier, mono = Notifier(), FakeMonotonic()
    executor, gateway, _broker = build(st, notifier=notifier, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True

    reborn = PaperAutoExecutor(
        st, gateway, window_seconds=WINDOW, notify=notifier, monotonic=mono, budget=BIG,
        risk_recheck_factory=approved_recheck, positions_fn=lambda: [])
    assert reborn.recover_orphans() == [], "an executed ticket is not an abandoned promise"


# =============================================================================================
# 8. a stale auto-approval cannot authorise a DIFFERENT order row
# =============================================================================================
def test_an_auto_approval_binds_the_ORDER_ROW_not_just_the_tuple(st):
    """Gate (e) bound (proposal_id, risk_decision_id, order_tuple_hash) and nothing else, so the
    authority was order-row-agnostic. RW-17 is the first thing that routinely leaves UNCONSUMED
    approvals on the chain with no human anywhere to notice the orphan, so the signed payload's
    ``order_id`` is now part of the binding when it is present."""
    def rejecting_recheck(_order):
        return lambda _positions: {"decision": "rejected", "failed_checks": ["probe"]}

    mono = FakeMonotonic()
    executor, gateway, broker = build(st, mono=mono, recheck=rejecting_recheck)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)

    # A gate-(d) rejection AFTER the mint: this is the routine RW-17 artefact — an approval
    # sitting on the append-only chain, UNCONSUMED, for the rest of its 900s TTL, with no human
    # anywhere in the loop to notice the orphan.
    assert executor.resolve_due()[0].executed is False
    [approval] = lane_approvals(st)
    assert approval.single_use_consumed is False
    assert broker.calls == []

    # A DIFFERENT order row under the SAME proposal / risk decision / canonical tuple — the
    # four fields gate (e) used to bind on. Everything the old check compared is identical.
    twin = create_order(
        st, proposal_id=pid, risk_decision_id=rd.id, account="paper", symbol=order.symbol,
        side=order.side, qty=order.qty, order_type="limit", limit_price=order.limit_price,
        tif="day", notional=order.notional, order_tuple_hash=order.order_tuple_hash,
        actor=COPYTRADE_RUNTIME)
    assert twin.id != order.id

    # Mint the replay-protection token the way any caller holding the gateway would.
    # ``mint_submit_token``'s own docstring is explicit that the token is NOT the authority — the
    # chain-anchored approval is — so this removes the incidental backstop and leaves gate (e)
    # to be the thing that refuses. Without the order-row binding the twin SUBMITS here.
    gateway.mint_submit_token(nonce="n-twin", proposal_id=pid, risk_decision_id=rd.id,
                              order_tuple_hash=order.order_tuple_hash)

    outcome = gateway.place_equity_order(
        twin, approval=approval, risk_decision=rd, ladder=executor._ladder(),
        breaker_engine=executor._breakers(), risk_recheck=approved_recheck(twin),
        positions=[], token_nonce="n-twin", producing_runtime=COPYTRADE_RUNTIME)

    assert outcome.ok is False, "a different order row may not ride a stale auto-approval"
    assert outcome.stage == "approval"
    assert broker.calls == []
    assert order_state(st, twin.id) == "proposed"


# =============================================================================================
# 9. SEC-07 pending exposure gets its own diagnosable name
# =============================================================================================
def test_a_pending_exposure_refusal_is_named_separately_from_a_generic_one(st):
    """A non-crossing paper LIMIT order acks as ``accepted``, which no state-machine edge
    consumes until RW-15's order-status poll ships, so the approved-but-unfilled ledger only
    ever grows. The operator reading a generic ``gateway_refused`` goes looking at the filing;
    they need to be told to look at the exposure ledger."""
    class Accepted:
        def __init__(self):
            self.calls = []

        def submit_order(self, request):
            self.calls.append(dict(request))
            return {"broker_order_id": f"paper-{len(self.calls)}", "status": "accepted"}

    broker = Accepted()
    gateway = OrderGateway(broker, st=st, execution_scope="paper",
                           pending_exposure_cap=Decimal("120"))
    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, _b = build(st, notifier=notifier, mono=mono, gateway=gateway, broker=broker)

    reasons = []
    for n in range(4):
        o, rd, pid = make_order(st, symbol=f"SYM{n}", notional="50", limit_price="50", qty="1")
        executor.schedule(order=o, risk_decision=rd, proposal_id=pid)
        mono.advance(WINDOW + 1)
        [r] = [x for x in executor.resolve_due() if x.proposal_id == pid]
        reasons.append(r.reason)

    assert reasons[:2] == [REASON_EXECUTED, REASON_EXECUTED]
    assert reasons[2:] == [REASON_PENDING_EXPOSURE, REASON_PENDING_EXPOSURE]
    assert notifier.last(NOTIFY_REFUSED)["refused_by"] == REASON_PENDING_EXPOSURE


# =============================================================================================
# 10. the open-position rail reads the ACCOUNT, not only its own adjudications
# =============================================================================================
def test_a_position_closed_outside_the_lane_frees_its_slot(st):
    """``open_symbols`` was released only by a LANE sell. A name the lane bought and the
    OPERATOR sold by hand — through the human paper gate, whose adjudication carries no ``lane``
    marker — was never released, so with the shipped default of ten the rail ratcheted to a
    permanent floor and reported a stale ledger as an exhausted budget."""
    from brokers.order_state_machine import transition

    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, pid = make_order(st, symbol="AAPL")
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True
    assert budget_state(st, day=st.clock.now().date()).open_symbols == frozenset({"AAPL"})

    # The operator exits by hand: a NON-lane sell order that reaches the broker.
    exit_order = create_order(
        st, proposal_id=new_id("prop"), risk_decision_id=rd.id, account="paper", symbol="AAPL",
        side="sell", qty="1", order_type="limit", limit_price="50", tif="day", notional="50",
        order_tuple_hash="h-exit", actor="human")
    transition(st, exit_order, "approved", actor="human")
    transition(st, exit_order, "submitted", actor="human")

    assert budget_state(st, day=st.clock.now().date()).open_symbols == frozenset()


# =============================================================================================
# 11. the daily digest is derived from the SIGNED chain, so a restart cannot hide the day
# =============================================================================================
def test_the_digest_survives_a_restart_because_it_reads_the_chain(st, _isolate_halt):
    """``PaperAutoExecutor.counts`` is an in-memory dict on one object: empty after every
    restart, and therefore guaranteed to under-report on exactly the days the digest is worth
    reading."""
    notifier, mono = Notifier(), FakeMonotonic()
    executor, gateway, _broker = build(st, notifier=notifier, mono=mono)
    a = make_order(st, symbol="AAPL")
    executor.schedule(order=a[0], risk_decision=a[1], proposal_id=a[2])
    mono.advance(WINDOW + 1)
    assert executor.resolve_due()[0].executed is True

    b = make_order(st, symbol="MSFT")
    executor.schedule(order=b[0], risk_decision=b[1], proposal_id=b[2])
    set_halt(_isolate_halt)
    mono.advance(WINDOW + 1)
    executor.resolve_due()
    mono.advance(executor.ticket_max_age_seconds)
    assert executor.resolve_due()[0].reason == REASON_TICKET_EXPIRED
    clear_halt(_isolate_halt)

    # A COMPLETELY FRESH PROCESS reads the same day off the chain.
    reborn = PaperAutoExecutor(
        st, gateway, window_seconds=WINDOW, notify=notifier, monotonic=mono, budget=BIG,
        risk_recheck_factory=approved_recheck, positions_fn=lambda: [])
    assert reborn.counts[REASON_EXECUTED] == 0, "in-memory counts are gone, as expected"

    rows = digest_rows(st, day=st.clock.now().date())
    assert rows.readable is True
    assert [t.symbol for t in rows.executed] == ["AAPL"]
    assert [(r.symbol, r.gate) for r in rows.refused] == [("MSFT", "ticket_expired")]


def test_an_unreadable_chain_is_an_unreadable_digest_not_a_quiet_day(st):
    with st.session_factory() as s, s.begin():
        head = s.execute(
            select(AuditEntry).order_by(AuditEntry.seq.desc()).limit(1)).scalar_one()
        s.delete(head)                                   # keyless tail truncation

    rows = digest_rows(st, day=st.clock.now().date())

    assert rows.readable is False and rows.error
    assert rows.executed == () and rows.refused == ()


def test_a_forged_ticket_entry_cannot_write_a_row_into_the_digest(st):
    mono = FakeMonotonic()
    executor, _gw, _broker = build(st, mono=mono)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)
    executor.resolve_due()

    with st.session_factory() as s, s.begin():
        row = s.execute(
            select(AuditEntry).where(AuditEntry.event_type == EVENT_AUTOEXEC_TICKET)
            .order_by(AuditEntry.seq.desc()).limit(1)).scalar_one()
        payload = dict(row.payload or {})
        payload["symbol"] = "FORGED"
        row.payload = payload                            # keyless edit: HMAC no longer covers it

    rows = digest_rows(st, day=st.clock.now().date())
    assert "FORGED" not in [t.symbol for t in rows.executed]


def test_every_settled_ticket_reaches_the_digest_under_a_KNOWN_gate(st):
    """The digest translates through the SAME table the live refusal uses, so the gate an
    operator reads at 10:41 and the one they read at 16:20 are the same string."""
    from copytrade.notify import KNOWN_REFUSAL_GATES

    notifier, mono = Notifier(), FakeMonotonic()
    executor, _gw, _broker = build(st, notifier=notifier, mono=mono, ticket_max_age=1)
    order, rd, pid = make_order(st)
    executor.schedule(order=order, risk_decision=rd, proposal_id=pid)
    mono.advance(WINDOW + 1)
    executor.resolve_due()

    rows = digest_rows(st, day=st.clock.now().date())
    assert rows.refused
    for refusal in rows.refused:
        assert refusal.gate in KNOWN_REFUSAL_GATES, refusal


# =============================================================================================
# 12. the vocabulary itself
# =============================================================================================
def test_every_refusable_reason_is_in_the_counted_vocabulary():
    assert autoexec_mod.REFUSABLE_REASONS <= set(autoexec_mod.ALL_RESOLUTION_REASONS)
    assert autoexec_mod.TRANSIENT_REASONS <= set(autoexec_mod.ALL_RESOLUTION_REASONS)


def test_the_reasons_that_touched_the_chain_are_never_transient():
    """Retrying after the approval exists would either replay a minted authority or mint a
    second one, so every post-mint outcome must be TERMINAL."""
    for reason in (REASON_APPROVAL_REPLAY, "approval_write_failed", "gateway_refused",
                   "mode_not_paper", "not_paper_transport", "clock_backwards", "cancelled"):
        assert reason not in TRANSIENT_REASONS, reason


@pytest.mark.parametrize("reason", sorted(autoexec_mod.REFUSABLE_REASONS))
def test_every_refusable_reason_renders_a_named_gate(reason):
    from copytrade import notify

    gate = notify.REFUSAL_GATE_BY_REASON.get(reason)
    assert gate in notify.KNOWN_REFUSAL_GATES, f"{reason!r} -> {gate!r}"

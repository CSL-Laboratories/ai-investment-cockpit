"""QT-07 — Tier C: the autonomous loop, its latency model, and its containment.

This is the only part of the engine with no human in the loop. Its safety argument is not
"we were careful" — it is four independent, tested mechanisms, and this file is where three
of them are proven (the fourth, the import gate, lives in ``tests/test_invariants.py``
because it is a property of the whole package rather than of any object).

The properties pinned:

  * THE LOOP REFUSES A NON-SIM VENUE, including a broker-shaped duck type. The check is
    ``isinstance`` on purpose: duck typing is exactly how a real broker would get in.
  * HALT AND WATCH-ONLY STOP IT WITHIN ONE EVENT. An operator's kill switch must not have to
    wait for the run to finish — nobody is watching this loop.
  * GUARDS BLOCK BEFORE THE BOOK IS TOUCHED, and the kill switch LATCHES.
  * CANCELS ARE ALWAYS PERMITTED, even mid-breach. Blocking them would strand live orders at
    the exact moment the system decided something was wrong.
  * ORDERS ARE IN FLIGHT BEFORE THEY ARE LIVE. A zero-latency venue is the most flattering
    assumption available to a fast strategy, so it is never the default.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from quant.events import PriceScale, Side
from quant.execution.autoloop import AutoLoop, VenueContainmentError
from quant.execution.sim_venue import LatencyModel, SimVenue
from quant.feed.synthetic import SyntheticFeed
from quant.guards import GuardLimits, Guards
from quant.oms import CancelOrder, NewOrder
from quant.strategies.base import Strategy
from quant.strategies.market_maker import MarketMaker, MarketMakerParams

PENNY = PriceScale(tick_size=Decimal("0.01"))


class Spammer(Strategy):
    """Emits one order on every book update. For exercising rate and position limits."""

    def __init__(self, name="spam", qty=100, price=9_900, side=Side.BUY):
        self.qty, self.price, self.side = qty, price, side
        Strategy.__init__(self, name=name, symbols=("S",))

    def on_book_update(self, ctx):
        return [NewOrder(client_id=self.next_client_id(), symbol="S", side=self.side,
                         qty=self.qty, price_ticks=self.price)]

    def describe(self):
        return {"strategy": "spammer", "name": self.name}


def _feed(n: int = 200, seed: int = 3):
    return list(SyntheticFeed(symbols=("S",), n_events=n, seed=seed))


# ---- containment #2: the type assertion ---------------------------------------------------


def test_the_loop_refuses_anything_that_is_not_a_sim_venue():
    class NotAVenue:
        symbol = "S"

    with pytest.raises(VenueContainmentError, match="different project"):
        AutoLoop(venue=NotAVenue(), strategies=[], scale=PENNY)


def test_the_loop_refuses_a_BROKER_SHAPED_DUCK_TYPE():
    """THE test that justifies isinstance over duck typing. An object carrying exactly the
    right attribute names is precisely how a real broker adapter would slip in — and under a
    duck-typed check it would be accepted, because it quacks correctly."""

    class BrokerShaped:
        symbol = "S"
        book = None
        latency = LatencyModel.instant()

        def accept_order(self, *a, **k):
            raise AssertionError("a real broker must never be reachable from Tier C")

        def cancel_order(self, *a, **k):
            return True

        def advance(self, ts):
            return []

        def apply_market_event(self, event):
            return []

        def visible_book(self, ts, levels=5):
            return None

    with pytest.raises(VenueContainmentError):
        AutoLoop(venue=BrokerShaped(), strategies=[], scale=PENNY)


def test_a_real_sim_venue_is_accepted():
    """CONTROL: a loop that refused everything would pass both tests above."""
    loop = AutoLoop(venue=SimVenue(symbol="S"), strategies=[], scale=PENNY)
    assert isinstance(loop.venue, SimVenue)


def test_the_containment_check_runs_before_any_event_is_processed():
    """Refusing at construction rather than at first order means a misconfigured loop cannot
    do anything at all, rather than doing part of a run and then stopping."""
    class NotAVenue:
        symbol = "S"

    with pytest.raises(VenueContainmentError):
        AutoLoop(venue=NotAVenue(), strategies=[Spammer()], scale=PENNY).run(_feed())


# ---- containment #3: halt and watch-only ------------------------------------------------------


def test_an_active_halt_stops_the_loop_within_one_event():
    halted = {"on": False}
    guards = Guards(halt_fn=lambda: halted["on"])
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel.instant()),
                    strategies=[Spammer()], scale=PENNY, guards=guards)

    events = _feed(200)

    def feed_that_halts_midway():
        for index, event in enumerate(events):
            if index == 20:
                halted["on"] = True
            yield event

    result = loop.run(feed_that_halts_midway())
    assert result.halted
    assert result.halt_reason == "HALT is set"
    assert result.events <= 22          # stopped essentially immediately, not at the end


def test_watch_only_stops_the_loop():
    guards = Guards(watch_only_fn=lambda: True)
    loop = AutoLoop(venue=SimVenue(symbol="S"), strategies=[Spammer()],
                    scale=PENNY, guards=guards)
    result = loop.run(_feed())
    assert result.halted
    assert result.halt_reason == "system is watch_only"
    assert result.events == 0


def test_the_loop_runs_when_neither_control_is_engaged():
    """CONTROL for both stop tests."""
    guards = Guards(halt_fn=lambda: False, watch_only_fn=lambda: False)
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel.instant()),
                    strategies=[Spammer()], scale=PENNY, guards=guards)
    result = loop.run(_feed(100))
    assert not result.halted
    assert result.events == 100


# ---- containment #4: the guards -----------------------------------------------------------------


def test_an_oversized_order_is_blocked_before_it_reaches_the_book():
    guards = Guards(limits=GuardLimits(max_order_qty=50))
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    loop = AutoLoop(venue=venue, strategies=[Spammer(qty=500)], scale=PENNY, guards=guards)
    result = loop.run(_feed(50))
    assert result.intents_submitted == 0
    assert result.intents_blocked > 0
    assert len(venue.book.our_orders()) == 0


def test_the_position_limit_holds_under_a_strategy_that_never_stops_buying():
    guards = Guards(limits=GuardLimits(max_position=300, max_orders_per_window=10_000))
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    # Price through the offer so every order fills and the position actually accumulates.
    loop = AutoLoop(venue=venue, strategies=[Spammer(qty=100, price=99_000)],
                    scale=PENNY, guards=guards)
    result = loop.run(_feed(400))
    assert abs(loop.oms.position("S")) <= 300
    assert result.intents_blocked > 0


def test_rate_window_matches_a_full_scan_including_a_backwards_stamp():
    """The deque drops a prefix on the way up. A stamp that goes backwards can
    expire an interior time, and that case still has to match the full scan."""
    guards = Guards(limits=GuardLimits(max_orders_per_window=3, order_rate_window_ns=100))
    intent = NewOrder(client_id="c", symbol="S", side=Side.BUY, qty=1, price_ticks=1)
    recent: list[int] = []
    for ts in (0, 10, 20, 30, 150, 50, 160, 40, 200):
        window_start = ts - 100
        recent = [stamp for stamp in recent if stamp >= window_start]
        allowed = len(recent) < 3
        if allowed:
            recent.append(ts)
        assert guards.check(intent, ts=ts).allowed is allowed
        assert list(guards._recent_orders) == recent
    guards.reset()
    assert guards.check(intent, ts=0).allowed
    assert list(guards._recent_orders) == [0]


def test_the_order_rate_ceiling_catches_a_requote_loop():
    """Not a financial limit — a blast-radius one. Every individual order here passes every
    exposure cap; only a rate ceiling catches this shape of failure."""
    guards = Guards(limits=GuardLimits(max_orders_per_window=5,
                                       order_rate_window_ns=10_000_000_000))
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel.instant()),
                    strategies=[Spammer()], scale=PENNY, guards=guards)
    result = loop.run(_feed(200))
    assert result.intents_submitted <= 6
    assert result.intents_blocked > 100


def test_the_kill_switch_LATCHES_and_does_not_re_arm():
    """A loop that re-armed itself after a breach would re-breach, and an operator would see
    a flapping counter instead of a stop."""
    guards = Guards()
    guards.trip("manual")
    assert not guards.check(NewOrder(client_id="c", symbol="S", side=Side.BUY,
                                     qty=1, price_ticks=1)).allowed
    guards.trip("something else")
    assert guards.trip_reason == "manual"      # the FIRST reason is retained
    guards.reset()
    assert guards.check(NewOrder(client_id="c", symbol="S", side=Side.BUY,
                                 qty=1, price_ticks=1)).allowed


def test_a_dollar_session_loss_beyond_the_stop_trips_the_kill_switch():
    """BUG-047: the public limit names the same dollar unit emitted by ``PnLTracker``."""
    guards = Guards(limits=GuardLimits(max_loss_dollars=100.0))
    guards.observe_pnl(-50.0)
    assert not guards.tripped
    guards.observe_pnl(-150.0)
    assert guards.tripped
    assert "max_loss_dollars $100.0" in guards.trip_reason


def test_cancels_are_permitted_even_while_the_kill_switch_is_tripped():
    """Blocking cancels would strand live orders at exactly the moment the system decided
    something was wrong — the one time you most want to be able to withdraw."""
    guards = Guards(halt_fn=lambda: True)
    guards.trip("breach")
    assert guards.check(CancelOrder(client_id="c1")).allowed


def test_gross_exposure_counts_orders_still_in_flight():
    """A limit that only counts acknowledged orders is one you can exceed by sending fast —
    which is the capability an autonomous loop has and a human does not."""
    from quant.oms import OMS

    oms = OMS()
    guards = Guards(limits=GuardLimits(max_gross_exposure_ticks=1_000_000,
                                       max_position=10 ** 9, max_order_qty=10 ** 9))
    intent = NewOrder(client_id="a", symbol="S", side=Side.BUY, qty=1_000, price_ticks=600)
    oms.register(intent, strategy="s")          # in flight, never acknowledged
    assert oms.get("a").state.value == "pending_new"
    second = NewOrder(client_id="b", symbol="S", side=Side.BUY, qty=1_000, price_ticks=600)
    assert not guards.check(second, oms=oms).allowed


def test_autoloop_keeps_an_order_pending_until_the_venue_releases_it():
    """BUG-048: sending is not an ACK; the latency interval must remain observable."""
    venue = SimVenue(
        symbol="S",
        latency=LatencyModel(order_entry_ns=100, market_data_ns=0, cancel_ns=0),
    )
    loop = AutoLoop(venue=venue, strategies=[], scale=PENNY)
    strategy = Spammer()
    intent = NewOrder(
        client_id="in-flight", symbol="S", side=Side.BUY, qty=10, price_ticks=9_900
    )

    loop._route(strategy, [intent], ts=10)
    assert loop.oms.get("in-flight").state.value == "pending_new"
    assert venue.advance(109) == []
    assert loop.oms.get("in-flight").state.value == "pending_new"

    loop._absorb_outcomes(venue.advance(110))
    assert loop.oms.get("in-flight").state.value == "working"


# ---- the latency model ------------------------------------------------------------------------------


def test_an_order_is_in_flight_before_it_is_live():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=1_000))
    intent = NewOrder(client_id="c1", symbol="S", side=Side.BUY, qty=100, price_ticks=9_900)
    arrive = venue.accept_order(intent, owner="s", ts=0)

    assert arrive == 1_000
    assert venue.in_flight == 1
    assert "c1" not in venue.book                # not live yet

    venue.advance(999)
    assert "c1" not in venue.book                # still not
    venue.advance(1_000)
    assert "c1" in venue.book                    # now
    assert venue.in_flight == 0


def test_an_in_flight_order_can_be_cancelled_before_it_arrives():
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=1_000))
    venue.accept_order(NewOrder(client_id="c1", symbol="S", side=Side.BUY, qty=100,
                                price_ticks=9_900), owner="s", ts=0)
    assert venue.cancel_order("c1")
    venue.advance(10_000)
    assert "c1" not in venue.book
    assert venue.expired_in_flight == 1


def test_orders_are_released_in_arrival_order_with_a_stable_tiebreak():
    """Deterministic release order is what keeps a replay reproducible when two orders land
    in the same nanosecond."""
    venue = SimVenue(symbol="S", latency=LatencyModel(order_entry_ns=100))
    for i in range(5):
        venue.accept_order(NewOrder(client_id=f"c{i}", symbol="S", side=Side.BUY,
                                    qty=100, price_ticks=9_900), owner="s", ts=0)
    venue.advance(1_000)
    resting = [o.order_id for o in venue.book.our_orders()]
    assert resting == [f"c{i}" for i in range(5)]


def test_the_visible_book_is_stamped_back_by_the_market_data_delay():
    venue = SimVenue(symbol="S", latency=LatencyModel(market_data_ns=5_000))
    venue.book.add(order_id="v", side=Side.BUY, price_ticks=100, qty=100)
    assert venue.visible_book(ts=1_000_000).ts == 995_000


def test_the_instant_model_is_available_but_is_not_the_default():
    """A zero-latency venue lets a strategy react at the instant of a move and always win
    the queue. It exists for unit tests and must never be what you get by accident."""
    assert not SimVenue(symbol="S").latency.is_instant
    assert LatencyModel.instant().is_instant


# ---- the loop end to end -------------------------------------------------------------------------------


def test_a_full_autonomous_session_trades_and_reports_attribution():
    venue = SimVenue(symbol="S", latency=LatencyModel())
    mm = MarketMaker(name="mm", symbols=("S",),
                     params=MarketMakerParams(quote_size=100, max_inventory=1_000,
                                              requote_threshold_ticks=2))
    loop = AutoLoop(venue=venue, strategies=[mm], scale=PENNY,
                    guards=Guards(limits=GuardLimits(max_orders_per_window=100_000)),
                    timer_interval_ns=2_000_000)
    result = loop.run(SyntheticFeed(symbols=("S",), n_events=4_000, seed=3))

    venue.book.check_integrity()
    assert result.events == 4_000
    assert result.intents_submitted > 0
    assert result.attribution is not None
    assert {"spread_capture", "inventory_drift", "fees", "total"} <= set(result.attribution)


def test_a_session_is_written_to_the_audit_sink_at_both_ends():
    """A simulated run that left no trace would be the only activity in this repo that did."""
    written: list[tuple[str, dict]] = []
    loop = AutoLoop(venue=SimVenue(symbol="S", latency=LatencyModel.instant()),
                    strategies=[Spammer()], scale=PENNY,
                    audit_sink=lambda event_type, payload: written.append((event_type, payload)))
    loop.run(_feed(50))

    phases = [p["phase"] for _, p in written if "phase" in p]
    assert phases == ["start", "end"]
    assert written[-1][1]["events"] == 50


def test_a_breach_is_audited_with_its_reason():
    written: list[tuple[str, dict]] = []
    guards = Guards(halt_fn=lambda: True)
    loop = AutoLoop(venue=SimVenue(symbol="S"), strategies=[Spammer()], scale=PENNY,
                    guards=guards,
                    audit_sink=lambda event_type, payload: written.append((event_type, payload)))
    loop.run(_feed(20))
    breaches = [p for t, p in written if t == "quant_sim_breach"]
    assert breaches and breaches[0]["reason"] == "HALT is set"


# ---- the loop reconciles the OMS with what the venue actually did -------------------------


def _stranded_ids(oms, book) -> list[str]:
    """Live in the OMS but absent from the book. An order still RESTING is correctly live —
    conflating the two would make a working maker look broken at the end of every run."""
    return [o.client_id for o in oms.live_orders() if o.client_id not in book]


def test_a_remainder_that_never_rested_does_not_strand_the_loop():
    """THE UNATTENDED LOOP MUST NOT GO SILENT. ``Runner._place`` reconciles the OMS when an
    aggressive order leaves no resting remainder, and says why in a comment: 'it stays live
    forever and every strategy that checks its own open orders goes permanently silent — a
    deadlock that looks like a strategy with no signal'.

    ``AutoLoop`` registered, sent and acked, then never reconciled — reproducing that exact
    bug in the ONE component with no human watching. Nothing reported it either:
    ``AutoLoopResult`` had no field for a stranded order, so the run looked clean while the
    strategy did a few percent of its work and stopped.
    """
    from quant.runner import Runner

    events = _feed(4_000)
    mm = lambda: MarketMaker(name="mm", symbols=("S",),                      # noqa: E731
                             params=MarketMakerParams(requote_threshold_ticks=1))

    runner = Runner(feed=iter(list(events)), strategies=[mm()], scale=PENNY)
    r_result = runner.run()

    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    loop = AutoLoop(venue=venue, strategies=[mm()], scale=PENNY)
    a_result = loop.run(iter(list(events)))

    stranded = _stranded_ids(loop.oms, venue.book)
    assert not stranded, f"orders live in the OMS but absent from the book: {stranded}"
    assert a_result.stranded_orders == 0, "the run must also REPORT that it stranded nothing"

    # The real symptom was throughput collapse: 6 submissions against the Runner's 126.
    assert a_result.intents_submitted >= r_result.intents_submitted // 2, (
        f"autoloop submitted {a_result.intents_submitted} vs runner "
        f"{r_result.intents_submitted} — the loop went silent"
    )


def test_a_self_trade_cancellation_is_reflected_in_the_oms():
    """``matching.py``'s own docstring says 'a market maker quoting both sides WILL eventually
    cross itself', so STP is the steady state, not an edge case. ``SimVenue._apply`` dropped
    ``MatchResult.stp_cancelled`` on the floor, leaving the OMS holding an order the book no
    longer has — which inflates exposure_ticks forever and makes the maker's own reconcile
    believe a pulled quote is still resting."""
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    loop = AutoLoop(venue=venue, strategies=[], scale=PENNY)

    resting = NewOrder(client_id="q-ask", symbol="S", side=Side.SELL, qty=100,
                       price_ticks=10_000)
    loop.oms.register(resting, strategy="mm", ts=0)
    venue.accept_order(resting, owner="mm", ts=0)
    loop.oms.on_acknowledged("q-ask", ts=0)
    venue.advance(0)

    crossing = NewOrder(client_id="q-bid", symbol="S", side=Side.BUY, qty=100,
                        price_ticks=10_000)
    loop.oms.register(crossing, strategy="mm", ts=1)
    venue.accept_order(crossing, owner="mm", ts=1)
    loop.oms.on_acknowledged("q-bid", ts=1)
    loop._absorb_outcomes(venue.advance(1))

    assert "q-ask" not in venue.book, "STP should have pulled the resting quote from the book"
    ask = loop.oms.get("q-ask")
    assert ask is not None and not ask.is_live, (
        f"OMS still believes the STP-cancelled quote is live (state={ask.state if ask else None})"
    )


def test_same_batch_fill_precedes_later_stp_cancellation_in_oms():
    """BUG-099: V partially fills as a taker and rests; the next wire-order release
    STP-cancels V's remainder. Venue outcomes are causal, but the old two-pass absorber
    cancelled every STP victim before applying any fill, causing the impossible transition
    CANCELLED -> PARTIALLY_FILLED and crashing the unattended loop."""
    venue = SimVenue(symbol="S", latency=LatencyModel.instant())
    venue.book.add(
        order_id="market-ask", side=Side.SELL, price_ticks=100, qty=40, owner=None,
    )
    loop = AutoLoop(venue=venue, strategies=[], scale=PENNY)

    victim = NewOrder(
        client_id="victim", symbol="S", side=Side.BUY, qty=100, price_ticks=100,
    )
    crossing = NewOrder(
        client_id="crossing", symbol="S", side=Side.SELL, qty=10, price_ticks=100,
    )
    for intent in (victim, crossing):
        loop.oms.register(intent, strategy="mm", ts=0)
        venue.accept_order(intent, owner="mm", ts=0)

    outcomes = venue.advance(0)
    loop._absorb_outcomes(outcomes)

    managed = loop.oms.get("victim")
    assert managed is not None
    assert managed.filled_qty == 40
    assert managed.state.value == "cancelled"
    assert loop.oms.position("S") == 40
    assert "victim" not in venue.book
    assert "crossing" in venue.book


# ---- guards apply to a replace, not only to a new order ------------------------------------


def _guarded(**limit_kwargs):
    from quant.oms import OMS
    limits = GuardLimits(**limit_kwargs)
    return Guards(limits=limits), OMS()


def _resting(oms, *, client_id="o1", qty=10, price=100):
    from quant.oms import NewOrder as _New
    intent = _New(client_id=client_id, symbol="S", side=Side.BUY, qty=qty, price_ticks=price)
    oms.register(intent, strategy="s", ts=0)
    oms.on_acknowledged(client_id, ts=0)
    return intent


def test_a_replace_cannot_exceed_the_order_size_limit():
    """EVERY LIMIT WAS BYPASSABLE BY REPLACING. ``check`` returned ok() for ReplaceOrder before
    the qty, position, exposure and rate checks — so an intent blocked as a NewOrder was
    allowed as a resize of a one-lot. The string 'ReplaceOrder' appeared in ZERO test files,
    which is how it survived."""
    from quant.oms import ReplaceOrder as _Replace
    guards, oms = _guarded(max_order_qty=10)
    _resting(oms)
    verdict = guards.check(_Replace(client_id="o1", new_qty=1_000_000_000),
                           oms=oms, ts=0)
    assert not verdict.allowed and "max_order_qty" in verdict.reason


def test_a_replace_cannot_exceed_the_gross_exposure_limit():
    from quant.oms import ReplaceOrder as _Replace
    guards, oms = _guarded(max_order_qty=10_000, max_position=10_000,
                           max_gross_exposure_ticks=5_000)
    _resting(oms, qty=10, price=100)          # 1,000 ticks of exposure
    verdict = guards.check(_Replace(client_id="o1", new_price_ticks=1_000, new_qty=10_000),
                           oms=oms, ts=0)
    assert not verdict.allowed and "gross exposure" in verdict.reason


def test_a_requote_loop_is_caught_by_the_rate_limit():
    """``order_rate_window_ns`` is documented as 'the only limit that catches' a strategy stuck
    in a requote loop emitting tens of thousands of orders a second — and a requote IS a
    ReplaceOrder. The existing rate test uses a strategy that emits NewOrder, so it proved
    nothing about the shape the docstring names."""
    from quant.oms import ReplaceOrder as _Replace
    guards, oms = _guarded(max_order_qty=10_000, max_position=1_000_000,
                           max_gross_exposure_ticks=10 ** 12,
                           max_orders_per_window=5, order_rate_window_ns=1_000_000_000)
    _resting(oms, qty=1, price=1)
    allowed = sum(1 for _ in range(1_000)
                  if guards.check(_Replace(client_id="o1", new_price_ticks=1, new_qty=1),
                                  oms=oms, ts=0).allowed)
    assert allowed == 5, f"{allowed} of 1000 same-nanosecond requotes were allowed"


def test_a_replace_is_still_allowed_when_it_breaches_nothing():
    """CONTROL. Guards that blocked every replace would pass the three tests above and break
    the maker, whose whole loop is reprice-in-place."""
    from quant.oms import ReplaceOrder as _Replace
    guards, oms = _guarded(max_order_qty=10_000, max_position=10_000,
                           max_gross_exposure_ticks=10 ** 9)
    _resting(oms, qty=10, price=100)
    assert guards.check(_Replace(client_id="o1", new_price_ticks=101), oms=oms, ts=0).allowed


def test_a_cancel_is_still_permitted_after_the_kill_switch():
    """CONTROL for the ordering of the new branch: the cancel escape hatch must stay ABOVE
    everything, including the replace checks. Blocking cancels would strand live orders at the
    exact moment the system decided something was wrong."""
    guards, oms = _guarded()
    guards.trip("test")
    assert guards.check(CancelOrder(client_id="o1"), oms=oms, ts=0).allowed

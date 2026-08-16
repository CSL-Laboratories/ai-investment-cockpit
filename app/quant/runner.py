"""QT-03 — the event loop. One place where an event becomes a change to the world.

THE SHAPE
---------
    for event in feed:              # ordered by recv_ts, never exchange_ts
        apply it to the book(s)     # market flow
        build an immutable context
        ask each strategy for intents
        route intents through guards, the OMS, and the matcher
        feed resulting fills back to the OMS and the strategies

Everything above the feed is identical for synthetic data, a replayed capture, and a live
paper session. That is the payoff of the ``Feed`` protocol and of strategies returning data
instead of taking actions.

THE COUNTERFACTUAL PROBLEM, STATED RATHER THAN HIDDEN
-------------------------------------------------------
When a capture says "500 shares traded at 101", the venue's own book decided who got filled.
Our book contains OUR orders too, which were not there on the day. So we cannot simply apply
the venue's per-order executions — that would make our orders unfillable, and a market maker
would trade exactly never.

What ``apply_market_event`` does instead: a reported trade is replayed as an AGGRESSIVE ORDER
of that size through our real matcher. It consumes our book FIFO, so our resting orders fill
if and only if they were genuinely ahead in the queue. That is the correct queue-position
rule and it is the entire reason for building a real LOB.

The honest caveat: our presence would have changed the day. Orders we filled would have gone
to someone else; our quotes might have changed what others did. This is unavoidable in any
replay and is not "solved" here — it is bounded. The bound is that we never fill ahead of
our queue position, so the simulation cannot be optimistic about the one thing it models.
``RunResult.desync_cancels`` reports where our book and the venue's diverged so the size of
the divergence is a number rather than an assumption.

GUARDS RUN ON INTENTS, BEFORE ANYTHING ACTS ON THEM
-----------------------------------------------------
Every intent passes ``guards.check`` before reaching the book. Pre-trade means pre-trade: a
limit checked after the order is live is a report, not a control.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from quant.book.lob import LimitOrderBook, OrderNotFound
from quant.book.matching import AggressiveOrder, TimeInForce, match
from quant.events import BookAction, BookUpdate, Event, Fill, PriceScale, Side, Trade
from quant.costs import FeeSchedule
from quant.oms import OMS, CancelOrder, Intent, NewOrder, ReplaceOrder
from quant.pnl import PnLTracker
from quant.strategies.base import Strategy, StrategyContext

VENUE_PREFIX = "v:"
"""Namespace for venue order ids, so a capture's order 7 can never collide with our
client id 7. A collision would silently merge someone else's queue position with ours."""

_TIF = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC, "fok": TimeInForce.FOK}


@dataclass(slots=True)
class RunResult:
    """What happened. Deliberately includes the things that went WRONG, not just the PnL."""

    events: int = 0
    market_trades: int = 0
    fills: list[Fill] = field(default_factory=list)
    intents_submitted: int = 0
    intents_blocked: int = 0
    desync_cancels: int = 0
    """Cancels naming an order our book did not hold. In a replay this measures how far our
    book has diverged from the venue's — a number, so the divergence is bounded rather than
    assumed away. A large count invalidates the run and must not be discovered later."""

    blocked_reasons: dict[str, int] = field(default_factory=dict)
    final_positions: dict[str, int] = field(default_factory=dict)

    @property
    def our_trade_count(self) -> int:
        return len(self.fills)


def apply_market_event(book: LimitOrderBook, event: Event) -> list[Fill]:
    """Fold one market event into a book. THE only place event semantics are defined.

    Returns any fills of OURS the event caused — which is how a passive strategy gets filled
    without anyone drawing a random number.
    """
    if isinstance(event, BookUpdate):
        return _apply_book_update(book, event)
    if isinstance(event, Trade):
        return _apply_trade(book, event)
    return []


def _apply_book_update(book: LimitOrderBook, update: BookUpdate) -> list[Fill]:
    order_id = f"{VENUE_PREFIX}{update.order_ref}" if update.order_ref is not None else None
    if update.action is BookAction.ADD:
        if order_id is None or order_id in book:
            return []
        # Routed through the MATCHER, not rested unconditionally. A venue never rests an
        # order that crosses, and two things go wrong if we do:
        #
        #   * the book stays crossed forever once the capture and our reconstruction differ
        #     by a level, and every subsequent quote is computed off a nonsense mid;
        #   * our resting quotes never receive maker fills, because incoming aggression is
        #     silently parked beside them instead of trading against them. A market maker
        #     then only ever fills by crossing, which inverts the strategy it is modelling.
        #
        # The remainder rests, which is what an ADD message means when it does not cross.
        result = match(
            book,
            AggressiveOrder(order_id=order_id, side=update.side, qty=update.qty,
                            price_ticks=update.price_ticks, tif=TimeInForce.DAY, owner=None),
            seq=update.seq, exchange_ts=update.exchange_ts,
            recv_ts=update.recv_ts, ingest_ts=update.ingest_ts,
        )
        return result.fills

    if update.action is BookAction.CANCEL:
        if order_id is None:
            return []
        # OrderNotFound is allowed to escape to the caller, which counts it. The BOOK refuses
        # to hide a desync (see lob.OrderNotFound); the RUNNER decides what to do about one.
        # That split is deliberate: state must not lie, policy must not crash a long replay.
        book.cancel(order_id)
        return []

    if update.action is BookAction.REPLACE:
        if order_id is None:
            return []
        book.replace(order_id, new_price_ticks=update.price_ticks, new_qty=update.qty,
                     seq=update.seq, ts=update.exchange_ts)
        return []

    # EXECUTE: replay it as aggression against our book so queue priority decides who fills.
    return _cross(book, side=update.side.opposite, price_ticks=update.price_ticks,
                  qty=update.qty, event=update)


def _apply_trade(book: LimitOrderBook, trade: Trade) -> list[Fill]:
    return _cross(book, side=trade.aggressor, price_ticks=trade.price_ticks,
                  qty=trade.qty, event=trade)


def _cross(book: LimitOrderBook, *, side: Side, price_ticks: int, qty: int, event: Event) -> list[Fill]:
    """Replay reported aggression through the real matcher. IOC — it must not rest.

    A reported trade is a completed event; leaving a remainder resting would inject
    liquidity the market never had, and that phantom depth would then fill our orders.
    """
    result = match(
        book,
        AggressiveOrder(order_id=f"{VENUE_PREFIX}agg:{event.seq}", side=side, qty=qty,
                        price_ticks=price_ticks, tif=TimeInForce.IOC, owner=None),
        seq=event.seq, exchange_ts=event.exchange_ts,
        recv_ts=event.recv_ts, ingest_ts=event.ingest_ts,
    )
    return result.fills


@dataclass(slots=True)
class Runner:
    """Drives a feed through books, strategies, guards and the OMS.

    Holds no venue handle and imports nothing from ``brokers`` — see ``quant/__init__.py``
    for why that is a containment property and not an accident.
    """

    feed: Iterable[Event]
    strategies: Sequence[Strategy]
    scale: PriceScale
    guards: object | None = None
    timer_interval_ns: int = 0
    snapshot_levels: int = 5
    schedule: FeeSchedule | None = None
    """When supplied, the runner tracks PnL attribution inline. Inline rather than
    reconstructed afterwards because inventory drift depends on the mid path BETWEEN fills —
    information a fill log does not contain. Reconstructing it from the fills alone forces
    the caller to invent a mid, and the natural invention (use the fill price) makes spread
    capture identically zero by construction, which reads as 'this strategy captures no
    spread' rather than as a missing input."""

    books: dict[str, LimitOrderBook] = field(default_factory=dict, init=False)
    oms: OMS = field(default_factory=OMS, init=False)
    result: RunResult = field(default_factory=RunResult, init=False)
    pnl: PnLTracker | None = field(default=None, init=False)
    _next_timer: int = field(default=0, init=False, repr=False)
    _ctx_dirty: bool = field(default=False, init=False, repr=False)
    """Set by the two things that change what a strategy would see: absorbing a fill, and
    routing an intent that reaches the book. Read in the dispatch loops to decide whether the
    shared StrategyContext must be rebuilt."""

    equity_sample_ns: int = 0
    """Event-time grid for the PnL equity curve, fed to ``quant.stats``. 0 disables it."""

    def __post_init__(self) -> None:
        if self.schedule is not None:
            self.pnl = PnLTracker(scale=self.scale, schedule=self.schedule,
                                  equity_sample_ns=self.equity_sample_ns)

    def book_for(self, symbol: str) -> LimitOrderBook:
        book = self.books.get(symbol)
        if book is None:
            book = LimitOrderBook(symbol)
            self.books[symbol] = book
        return book

    def context(self, ts: int, primary: str | None = None) -> StrategyContext:
        return StrategyContext(
            ts=ts,
            books={s: b.snapshot(levels=self.snapshot_levels) for s, b in self.books.items()},
            positions=dict(self.oms.positions()),
            live_orders=tuple(self.oms.live_orders()),
            scale=self.scale,
            primary_symbol=primary,
        )

    def run(self) -> RunResult:
        for event in self.feed:
            book = self.book_for(event.symbol)

            # MARK BEFORE APPLYING THE EVENT. The mid a fill is measured against must be the
            # one that prevailed while we were RESTING, not the one left behind by the trade
            # that filled us.
            #
            # Marking afterwards looks equivalent and is not: when a seller sweeps our bid,
            # the post-sweep mid sits BELOW our fill price, so the fill scores as negative
            # spread capture. A market maker with a 78% maker ratio then reports itself as
            # paying the spread on every passive fill, which reads as "this strategy is
            # crossing" — the exact diagnosis the attribution exists to make, arrived at
            # backwards. The adverse selection is real, but it belongs to inventory drift and
            # the next mark picks it up there.
            if self.pnl is not None:
                self.pnl.mark(book.mid_ticks, event.ingest_ts)

            try:
                fills = apply_market_event(book, event)
            except OrderNotFound:
                # Counted, not fatal. See RunResult.desync_cancels — a replay that aborts on
                # the first dropped message tells you nothing; one that silently continues
                # tells you something false. Counting tells you how much to trust the run.
                self.result.desync_cancels += 1
                fills = []

            self.result.events += 1
            if isinstance(event, Trade):
                self.result.market_trades += 1

            # The context is rebuilt only when the world has actually CHANGED, tracked by
            # ``_ctx_dirty``. Building it walks the book and the live-order set, and the
            # previous code rebuilt it once per strategy per event unconditionally.
            #
            # Sharing it unconditionally would be wrong, and wrong in a way that is easy to
            # miss: absorbing a fill moves the position, and routing one strategy's intents
            # changes the live-order set the next strategy must see. A stale context makes a
            # strategy quote against a position it no longer holds — which looks like a
            # strategy bug, not a caching one. So the flag is set by exactly the two things
            # that mutate state, and the rebuild is skipped only when nothing happened.
            ctx = self.context(event.ingest_ts, primary=event.symbol)
            self._ctx_dirty = False
            self._absorb_fills(fills, ctx)

            for strategy in self._for_symbol(event.symbol):
                if self._ctx_dirty:
                    ctx = self.context(event.ingest_ts, primary=event.symbol)
                    self._ctx_dirty = False
                intents = self._dispatch(strategy, event, ctx)
                self._route(strategy, intents, event)

            self._maybe_timer(event)

        self.result.final_positions = dict(self.oms.positions())
        return self.result

    # ---- dispatch --------------------------------------------------------------------

    def _for_symbol(self, symbol: str) -> list[Strategy]:
        """A strategy sees an event only if it named the symbol, or named none at all.

        Pairs strategies name two symbols and must wake on either leg, so membership rather
        than equality with a single 'primary' symbol is the test.
        """
        return [s for s in self.strategies if not s.symbols or symbol in s.symbols]

    def _dispatch(self, strategy: Strategy, event: Event, ctx: StrategyContext) -> list[Intent]:
        intents = list(strategy.on_book_update(ctx))
        if isinstance(event, Trade):
            intents += strategy.on_trade(event, ctx)
        return intents

    def _maybe_timer(self, event: Event) -> None:
        if self.timer_interval_ns <= 0:
            return
        if self._next_timer == 0:
            self._next_timer = event.ingest_ts + self.timer_interval_ns
            return
        while event.ingest_ts >= self._next_timer:
            fire_at = self._next_timer
            self._next_timer += self.timer_interval_ns
            ctx = self.context(fire_at)
            self._ctx_dirty = False
            for strategy in self.strategies:
                if self._ctx_dirty:                    # a previous strategy placed an order
                    ctx = self.context(fire_at)
                    self._ctx_dirty = False
                self._route(strategy, list(strategy.on_timer(fire_at, ctx)), event)

    # ---- routing ---------------------------------------------------------------------

    def _route(self, strategy: Strategy, intents: list[Intent], event: Event) -> None:
        for intent in intents:
            if not self._permitted(strategy, intent, event):
                continue
            self.result.intents_submitted += 1
            self._ctx_dirty = True          # the live-order set the next strategy sees moved
            if isinstance(intent, NewOrder):
                self._place(strategy, intent, event)
            elif isinstance(intent, CancelOrder):
                self._cancel(intent)
            elif isinstance(intent, ReplaceOrder):
                self._replace(intent, event)

    def _permitted(self, strategy: Strategy, intent: Intent, event: Event) -> bool:
        """Pre-trade check. A limit evaluated after the order is live is a report, not a control."""
        if self.guards is None:
            return True
        verdict = self.guards.check(intent, strategy=strategy, oms=self.oms,
                                    books=self.books, ts=event.ingest_ts)
        if verdict.allowed:
            return True
        self.result.intents_blocked += 1
        reasons = self.result.blocked_reasons
        reasons[verdict.reason] = reasons.get(verdict.reason, 0) + 1
        return False

    def _place(self, strategy: Strategy, intent: NewOrder, event: Event) -> None:
        book = self.book_for(intent.symbol)
        self.oms.register(intent, strategy=strategy.name, ts=event.ingest_ts)
        result = match(
            book,
            AggressiveOrder(order_id=intent.client_id, side=intent.side, qty=intent.qty,
                            price_ticks=intent.price_ticks,
                            tif=_TIF.get(intent.tif, TimeInForce.DAY), owner=strategy.name),
            seq=event.seq, exchange_ts=event.exchange_ts,
            recv_ts=event.recv_ts, ingest_ts=event.ingest_ts,
        )
        self.oms.on_acknowledged(intent.client_id, ts=event.ingest_ts)
        for cancelled in result.stp_cancelled:
            if self.oms.get(cancelled) is not None:
                self.oms.on_cancelled(cancelled, ts=event.ingest_ts, reason="self_trade_prevention")

        ctx = self.context(event.ingest_ts, primary=intent.symbol)
        self._absorb_fills(result.fills, ctx)

        # An IOC/FOK remainder never rested, so the OMS must be told the order is done. Left
        # untold, it stays 'live' forever and every strategy that checks its own open orders
        # goes permanently silent — a deadlock that looks like a strategy with no signal.
        order = self.oms.get(intent.client_id)
        if order is not None and order.is_live and result.resting_order_id is None:
            self.oms.on_cancelled(intent.client_id, ts=event.ingest_ts, reason=result.status.value)

    def _cancel(self, intent: CancelOrder) -> None:
        order = self.oms.get(intent.client_id)
        if order is None or not order.is_live:
            return
        book = self.book_for(order.symbol)
        if intent.client_id in book:
            book.cancel(intent.client_id)
        self.oms.on_cancelled(intent.client_id, reason="strategy_cancel")

    def _replace(self, intent: ReplaceOrder, event: Event) -> None:
        order = self.oms.get(intent.client_id)
        if order is None or not order.is_live:
            return
        book = self.book_for(order.symbol)
        if intent.client_id not in book:
            return
        book.replace(intent.client_id, new_price_ticks=intent.new_price_ticks,
                     new_qty=intent.new_qty, seq=event.seq, ts=event.exchange_ts)

    def _absorb_fills(self, fills: list[Fill], ctx: StrategyContext) -> None:
        for fill in fills:
            order = self.oms.get(fill.order_id)
            if order is None:
                continue
            self.oms.on_fill(fill)
            self.result.fills.append(fill)
            self._ctx_dirty = True          # position moved; a stale context would hide it
            if self.pnl is not None:
                # No mid passed: the tracker already marked at this event's timestamp above,
                # so its ``_last_mid`` is the mid the fill happened against. Passing one here
                # would mark twice and double-count the interval's inventory drift.
                self.pnl.on_fill(fill)
            for strategy in self.strategies:
                if strategy.name == order.strategy:
                    self._route(strategy, list(strategy.on_fill(fill, ctx)), fill)

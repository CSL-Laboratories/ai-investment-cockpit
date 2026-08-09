"""QT-07 — the autonomous loop. No human in the loop, simulated venue only.

WHAT MAKES THIS DIFFERENT FROM ``runner.Runner``
--------------------------------------------------
``Runner`` is the research loop: it drives strategies through a book and reports what
happened. ``AutoLoop`` is the same shape with the properties an UNATTENDED loop needs:

    * a ``SimVenue`` with a latency model, so an order is in flight before it is live;
    * a type assertion that the venue is that class and not something broker-shaped;
    * HALT and watch-only checked every iteration, using the cockpit's existing controls;
    * guards on every intent, and a latching kill switch;
    * an audit trail, so a session is as inspectable as anything else the system does.

The separation is not cosmetic. ``Runner`` has no kill switch because it is driven by a
person watching its output; this one must be stoppable by an operator who is not watching.

THE CONTAINMENT, ONCE MORE
---------------------------
This module places orders with no approval step. It can do that safely for exactly one
reason: ``SimVenue`` is not a broker and nothing under ``quant/`` can import one. If you are
reading this because you want to point it at real execution, see
``quant/execution/__init__.py`` — the answer is that this is the wrong file to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Iterable, Sequence

from quant.book.lob import OrderNotFound
from quant.costs import FeeSchedule
from quant.events import Event, Fill, PriceScale, Trade
from quant.execution.sim_venue import SimVenue
from quant.guards import Guards
from quant.oms import OMS, CancelOrder, Intent, NewOrder, ReplaceOrder
from quant.pnl import PnLTracker
from quant.strategies.base import Strategy, StrategyContext

EVENT_QUANT_SIM_SESSION = "quant_sim_session"
EVENT_QUANT_SIM_BREACH = "quant_sim_breach"
"""Audit event types. A simulated session is written to the same hash-chained log as
everything else, because "it was only a simulation" is exactly the sentence that precedes
someone discovering it was not."""


class VenueContainmentError(RuntimeError):
    """The loop was handed something that is not a ``SimVenue``.

    Raised at construction, before any event is processed. This is containment #2 — a
    belt-and-braces check for a venue arriving from somewhere the import gate did not scan
    (a plugin, a pickle, a test double that grew up). It is an ``isinstance`` test rather
    than a duck-type check ON PURPOSE: duck typing is precisely how a broker-shaped object
    would get in, since any object with the right method names would otherwise qualify.
    """


@dataclass(slots=True)
class AutoLoopResult:
    events: int = 0
    fills: list[Fill] = field(default_factory=list)
    intents_submitted: int = 0
    intents_blocked: int = 0
    halted: bool = False
    halt_reason: str | None = None
    desync_cancels: int = 0
    """Feed events naming an order the venue's book did not hold. See ``runner.RunResult``
    for why this is a reported number rather than an exception or a silence."""
    blocked_reasons: dict[str, int] = field(default_factory=dict)
    final_position: int = 0
    attribution: dict | None = None

    @property
    def stopped_early(self) -> bool:
        return self.halted


@dataclass(slots=True)
class AutoLoop:
    """Drives strategies against a ``SimVenue`` with no approval step and hard limits."""

    venue: SimVenue
    strategies: Sequence[Strategy]
    scale: PriceScale
    guards: Guards = field(default_factory=Guards)
    schedule: FeeSchedule = field(default_factory=FeeSchedule)
    audit_sink: Callable[[str, dict], None] | None = None
    timer_interval_ns: int = 0

    oms: OMS = field(default_factory=OMS, init=False)
    pnl: PnLTracker = field(init=False)
    result: AutoLoopResult = field(default_factory=AutoLoopResult, init=False)
    _next_timer: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        # CONTAINMENT #2. Before anything else, and unconditional.
        if not isinstance(self.venue, SimVenue):
            raise VenueContainmentError(
                f"AutoLoop refuses a venue of type {type(self.venue).__name__}. Tier C places "
                "orders with NO human approval and NO risk gate, which is only safe because "
                "its venue is an in-process simulator. See quant/execution/__init__.py — "
                "pointing this at a broker is a different project, not a parameter."
            )
        self.pnl = PnLTracker(scale=self.scale, schedule=self.schedule)

    # ---- the loop ------------------------------------------------------------------------

    def run(self, feed: Iterable[Event]) -> AutoLoopResult:
        self._audit(EVENT_QUANT_SIM_SESSION, {"phase": "start", "symbol": self.venue.symbol,
                                              "strategies": [s.name for s in self.strategies]})
        for event in feed:
            # HALT is checked per EVENT, not per session. An operator hitting the kill switch
            # must stop the loop within one message, not at the end of the run — the whole
            # point of an unattended loop is that nobody is waiting to stop it politely.
            stop = self._stop_reason()
            if stop is not None:
                self.result.halted = True
                self.result.halt_reason = stop
                self._audit(EVENT_QUANT_SIM_BREACH, {"reason": stop, "at_event": self.result.events})
                break

            self.result.events += 1
            self._absorb(self.venue.advance(event.ingest_ts))
            try:
                self._absorb(self.venue.apply_market_event(event))
            except OrderNotFound:
                # Counted, never fatal — the same split as ``Runner``. The BOOK refuses to
                # hide a desync; the LOOP decides what to do about one. An unattended loop
                # that died on the first dropped message would turn a data-quality blip into
                # an outage, and one that hid it would report a run it cannot support.
                self.result.desync_cancels += 1

            book = self.venue.visible_book(event.ingest_ts)
            self.pnl.mark(book.mid_ticks, event.ingest_ts)
            self.guards.observe_pnl(float(self.pnl.snapshot().total))

            ctx = self._context(event.ingest_ts)
            for strategy in self.strategies:
                intents = list(strategy.on_book_update(ctx))
                if isinstance(event, Trade):
                    intents += strategy.on_trade(event, ctx)
                self._route(strategy, intents, event.ingest_ts)

            self._maybe_timer(event.ingest_ts)

        self.result.final_position = self.oms.position(self.venue.symbol)
        self.result.attribution = self.pnl.snapshot().as_dict()
        self._audit(EVENT_QUANT_SIM_SESSION, {
            "phase": "end", "events": self.result.events,
            "fills": len(self.result.fills), "blocked": self.result.intents_blocked,
            "halted": self.result.halted, "halt_reason": self.result.halt_reason,
            "attribution": self.result.attribution,
        })
        return self.result

    def _stop_reason(self) -> str | None:
        """The cockpit's own controls, not a private one. An operator's kill switch stops
        this loop the same way it stops everything else."""
        if self.guards.tripped:
            return f"kill switch: {self.guards.trip_reason}"
        if self.guards.halt_fn is not None and self.guards.halt_fn():
            return "HALT is set"
        if self.guards.watch_only_fn is not None and self.guards.watch_only_fn():
            return "system is watch_only"
        return None

    def _context(self, ts: int) -> StrategyContext:
        return StrategyContext(
            ts=ts,
            books={self.venue.symbol: self.venue.visible_book(ts)},
            positions=dict(self.oms.positions()),
            live_orders=tuple(self.oms.live_orders()),
            scale=self.scale,
            primary_symbol=self.venue.symbol,
        )

    def _maybe_timer(self, ts: int) -> None:
        if self.timer_interval_ns <= 0:
            return
        if self._next_timer == 0:
            self._next_timer = ts + self.timer_interval_ns
            return
        while ts >= self._next_timer:
            fire_at = self._next_timer
            self._next_timer += self.timer_interval_ns
            ctx = self._context(fire_at)
            for strategy in self.strategies:
                self._route(strategy, list(strategy.on_timer(fire_at, ctx)), fire_at)

    # ---- routing ---------------------------------------------------------------------------

    def _route(self, strategy: Strategy, intents: list[Intent], ts: int) -> None:
        for intent in intents:
            verdict = self.guards.check(intent, strategy=strategy, oms=self.oms,
                                        books={self.venue.symbol: self.venue.book}, ts=ts)
            if not verdict.allowed:
                self.result.intents_blocked += 1
                reasons = self.result.blocked_reasons
                key = verdict.reason.split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
                continue

            self.result.intents_submitted += 1
            if isinstance(intent, NewOrder):
                self.oms.register(intent, strategy=strategy.name, ts=ts)
                self.venue.accept_order(intent, owner=strategy.name, ts=ts)
                self.oms.on_acknowledged(intent.client_id, ts=ts)
            elif isinstance(intent, CancelOrder):
                order = self.oms.get(intent.client_id)
                if order is not None and order.is_live:
                    self.venue.cancel_order(intent.client_id, ts=ts)
                    self.oms.on_cancelled(intent.client_id, ts=ts, reason="strategy_cancel")
            elif isinstance(intent, ReplaceOrder):
                order = self.oms.get(intent.client_id)
                if order is not None and order.is_live and intent.client_id in self.venue.book:
                    self.venue.book.replace(intent.client_id,
                                            new_price_ticks=intent.new_price_ticks,
                                            new_qty=intent.new_qty, ts=ts)

    def _absorb(self, fills: list[Fill]) -> None:
        for fill in fills:
            order = self.oms.get(fill.order_id)
            if order is None:
                continue
            self.oms.on_fill(fill)
            self.result.fills.append(fill)
            self.pnl.on_fill(fill)
            ctx = self._context(fill.ingest_ts)
            for strategy in self.strategies:
                if strategy.name == order.strategy:
                    self._route(strategy, list(strategy.on_fill(fill, ctx)), fill.ingest_ts)

    def _audit(self, event_type: str, payload: dict) -> None:
        """Write to the cockpit's hash-chained log when a sink is injected.

        Optional rather than required so the loop stays usable in a notebook, but the sink is
        what makes a session reconstructible after the fact. A simulated run that left no
        trace would be the only activity in this repo that did not.
        """
        if self.audit_sink is not None:
            self.audit_sink(event_type, payload)


def total_from_attribution(attribution: dict | None) -> Decimal:
    """Read the net total out of a serialised attribution. Convenience for callers."""
    if not attribution:
        return Decimal("0")
    return Decimal(attribution.get("total", "0"))

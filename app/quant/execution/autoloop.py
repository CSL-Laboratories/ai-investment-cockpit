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

from copy import copy
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal
from heapq import heappop, heappush
from typing import Callable, Iterable, Sequence

from quant.book.lob import OrderNotFound
from quant.costs import FeeSchedule
from quant.events import Event, Fill, PriceScale, Trade
from quant.execution.sim_venue import (
    SimVenue, VenueCancelOutcome, VenueOutcome, VenueReplaceOutcome, VenueReport,
)
from quant.guards import Guards
from quant.oms import OMS, CancelOrder, IllegalTransition, Intent, NewOrder, ReplaceOrder
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
    desync_fills: int = 0
    """Fill reconciliation failures. Any failure invalidates accounting and stops the run."""
    accounting_valid: bool = True
    unreconciled_fills: list[Fill] = field(default_factory=list)
    """The refused fill and any later fills already produced in that venue batch.

    They remain evidence, never position or PnL authority. When accounting is invalid,
    final_position is only the last reconciled position and attribution is unavailable.
    """
    blocked_reasons: dict[str, int] = field(default_factory=dict)
    final_position: int = 0
    attribution: dict | None = None
    pending_reports: int = 0
    undelivered_fills: list[Fill] = field(default_factory=list)
    """Venue facts whose notifications have not arrived at the feed's final ingest time.

    These do not update observer position or strategy state. PnL is unavailable while
    known fills remain undelivered, and the ending audit retains their exact evidence.
    """
    stranded_orders: int = 0
    """Orders still live in the OMS when the run ended.

    Reported because the failure it detects is SILENT: an unreconciled order leaves the
    strategy waiting on a fill that can never arrive, and a loop doing a few percent of its
    work looks exactly like a loop with nothing to do. Nonzero here means the OMS and the
    venue disagree about what is outstanding — investigate before reading any other number,
    because every one of them was produced by a strategy that had stopped."""

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
    _last_exchange_ts: int = field(default=-1, init=False, repr=False)
    _last_ingest_ts: int = field(default=-1, init=False, repr=False)
    _report_seq: int = field(default=0, init=False, repr=False)
    _pending_reports: list[tuple[int, int, VenueReport | list[Fill]]] = field(
        default_factory=list, init=False, repr=False,
    )
    _awaiting_replacements: dict[str, list[ReplaceOrder]] = field(
        default_factory=dict, init=False, repr=False,
    )

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
            if (event.exchange_ts < self._last_exchange_ts
                    or event.ingest_ts < self._last_ingest_ts
                    or event.exchange_ts > event.ingest_ts
                    or event.recv_ts > event.ingest_ts):
                self._invalidate_accounting("invalid event chronology: exchange/ingest clock regression")
                break
            self._last_exchange_ts, self._last_ingest_ts = event.exchange_ts, event.ingest_ts
            # BUG-155: a cancellation at exchange time 120 must precede our order
            # arriving at 150, even when we do not ingest that cancellation until 200.
            # Never advance the venue beyond the next known exchange mutation.
            for report in self.venue.advance_reports(event.exchange_ts):
                self._queue_report(report.ts, report)
            if not self._deliver_reports(event.ingest_ts):
                break
            try:
                fills = self.venue.apply_market_event(event)
                if fills:
                    received = max(event.recv_ts,
                                   event.exchange_ts + self.venue.latency.market_data_ns)
                    delivery = max(event.ingest_ts, received)
                    self._queue_report(delivery, [
                        replace(fill, recv_ts=received, ingest_ts=delivery)
                        for fill in fills
                    ])
            except OrderNotFound:
                # Counted, never fatal — the same split as ``Runner``. The BOOK refuses to
                # hide a desync; the LOOP decides what to do about one. An unattended loop
                # that died on the first dropped message would turn a data-quality blip into
                # an outage, and one that hid it would report a run it cannot support.
                self.result.desync_cancels += 1
            if not self._deliver_reports(event.ingest_ts):
                break

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
        pending = [report for _ts, _seq, report in sorted(self._pending_reports)]
        self.result.pending_reports = len(pending)
        self.result.undelivered_fills = [fill for report in pending for fill in _report_fills(report)]
        self.result.attribution = (
            self.pnl.snapshot().as_dict()
            if self.result.accounting_valid and not self.result.undelivered_fills else None
        )
        # Orders resting on the book at the end are legitimately live; ones the book does not
        # hold are the stranded kind this counts.
        reporting_ids = {fill.order_id for fill in self.result.undelivered_fills}
        reporting_ids.update(report.client_id for report in pending if not isinstance(report, list))
        self.result.stranded_orders = sum(
            1 for order in self.oms.live_orders()
            if order.client_id not in self.venue.book and order.client_id not in reporting_ids
        )
        self._audit(EVENT_QUANT_SIM_SESSION, {
            "phase": "end", "events": self.result.events,
            "fills": len(self.result.fills), "blocked": self.result.intents_blocked,
            "halted": self.result.halted, "halt_reason": self.result.halt_reason,
            "accounting_valid": self.result.accounting_valid,
            "desync_fills": self.result.desync_fills,
            "unreconciled_fills": [_fill_evidence(f) for f in self.result.unreconciled_fills],
            "pending_reports": self.result.pending_reports,
            "undelivered_fills": [_fill_evidence(f) for f in self.result.undelivered_fills],
            "attribution": self.result.attribution,
        })
        return self.result

    def _queue_report(self, ts: int, report: VenueReport | list[Fill]) -> None:
        self._report_seq += 1
        heappush(self._pending_reports, (ts, self._report_seq, report))

    def _deliver_reports(self, ts: int) -> bool:
        # BUG-156: reports are handled only once their receive time has arrived.
        # This is an event-step simulation: a report between feed events is processed
        # at the next ingest tick, never by moving observer time ahead of that tick.
        while self._pending_reports and self._pending_reports[0][0] <= ts:
            _due, _seq, report = heappop(self._pending_reports)
            if isinstance(report, VenueOutcome):
                outcome = replace(report, ts=ts, result=replace(
                    report.result, fills=[replace(fill, ingest_ts=ts) for fill in report.fills],
                ))
                self._absorb_outcomes([outcome])
            elif isinstance(report, list):
                self._absorb([replace(fill, ingest_ts=ts) for fill in report])
            elif isinstance(report, VenueCancelOutcome):
                order = self.oms.get(report.client_id)
                if order is not None and order.is_live:
                    self.oms.on_cancelled(report.client_id, ts=ts, reason="venue_confirmed_cancel")
            elif isinstance(report, VenueReplaceOutcome):
                requests = self._awaiting_replacements.get(report.client_id)
                if requests:
                    requests.pop(0)
                    if not requests:
                        del self._awaiting_replacements[report.client_id]
                order = self.oms.get(report.client_id)
                if report.replaced and order is not None and order.is_live:
                    try:
                        self.oms.on_replaced(report.client_id, new_qty=report.new_qty,
                                             new_price_ticks=report.new_price_ticks, ts=ts)
                    except IllegalTransition as exc:
                        self._invalidate_accounting(f"invalid replacement accounting: {exc}")
                elif not report.replaced:
                    reasons = self.result.blocked_reasons
                    reasons["replace_missed"] = reasons.get("replace_missed", 0) + 1
            if not self.result.accounting_valid:
                # These venue facts already exist, even if their reports are still
                # traveling. Preserve the whole known suffix without further callbacks.
                for _due, _seq, remaining in sorted(self._pending_reports):
                    self.result.unreconciled_fills.extend(_report_fills(remaining))
                self._pending_reports.clear()
                break
        return self.result.accounting_valid

    def _stop_reason(self) -> str | None:
        """The cockpit's own controls, not a private one. An operator's kill switch stops
        this loop the same way it stops everything else."""
        if not self.result.accounting_valid:
            return self.result.halt_reason
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
            # A late timer carries its scheduled time separately; observations and
            # outgoing intents belong to the current processing time.
            ctx = self._context(ts)
            for strategy in self.strategies:
                self._route(strategy, list(strategy.on_timer(fire_at, ctx)), ts)

    # ---- routing ---------------------------------------------------------------------------

    def _guard_view(self) -> OMS:
        """Reserve worst-case pending replacement terms without changing observer state.

        A growing order can be live before its ACK reaches the OMS. Its increased
        size/price cannot fund a second order during that gap; a pending shrink also
        cannot release any of the existing reservation before acknowledgement.
        """
        if not self._awaiting_replacements:
            return self.oms
        # Read-only view. ``OMS.__copy__`` shares the live index with the real OMS
        # through a read-only proxy: read exposure and open-side size through it,
        # never route a venue callback (ack/fill/cancel) at it.
        view = copy(self.oms)
        view._orders = dict(self.oms._orders)
        for client_id, requests in self._awaiting_replacements.items():
            order = self.oms.get(client_id)
            if order is not None and order.is_live:
                view._orders[client_id] = replace(
                    order,
                    qty=max([order.qty] + [r.new_qty for r in requests if r.new_qty is not None]),
                    price_ticks=max([order.price_ticks or 0] + [
                        r.new_price_ticks for r in requests if r.new_price_ticks is not None
                    ]),
                )
        return view

    def _route(self, strategy: Strategy, intents: list[Intent], ts: int) -> None:
        if not self.result.accounting_valid:
            return
        for intent in intents:
            guard_view, guarded_intent = self._guard_view(), intent
            if (isinstance(intent, ReplaceOrder)
                    and intent.client_id in self._awaiting_replacements
                    and (intent.new_qty is None or intent.new_qty > 0)):
                order = guard_view.get(intent.client_id)
                if order is not None:
                    guarded_intent = replace(
                        intent, new_qty=max(order.qty, intent.new_qty or order.qty),
                        new_price_ticks=max(order.price_ticks or 0, intent.new_price_ticks or 0),
                    )
            verdict = self.guards.check(guarded_intent, strategy=strategy, oms=guard_view,
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
            elif isinstance(intent, CancelOrder):
                order = self.oms.get(intent.client_id)
                if order is not None and order.is_live:
                    # BUG-139: a TRAVELING cancel must not retire the OMS order at send
                    # time — the cancel can lose the withdraw-vs-fill race, and marking
                    # CANCELLED up front turned the later legitimate fill into an
                    # IllegalTransition that killed the run. The venue confirms on
                    # arrival; zero-latency cancels also wait for the exchange frontier.
                    self.venue.cancel_order(intent.client_id, ts=ts, defer=True)
            elif isinstance(intent, ReplaceOrder):
                order = self.oms.get(intent.client_id)
                if order is not None and order.is_live:
                    # BUG-078: the old guard also required ``client_id in self.venue.book``,
                    # so a reprice of an order still IN FLIGHT (accepted, not yet released
                    # by advance) was silently dropped — counted as submitted, applied
                    # nowhere — and the order later landed and filled at the OLD terms the
                    # strategy believed it had replaced. replace_order handles both homes;
                    # a replace that finds the order in neither (it just fully filled) is
                    # COUNTED, not swallowed.
                    if self.venue.replace_order(intent.client_id,
                                                new_qty=intent.new_qty,
                                                new_price_ticks=intent.new_price_ticks,
                                                ts=ts, defer=True):
                        # Submission is a request. OMS terms change only with the
                        # delivered venue acknowledgement, after any preceding fills.
                        self._awaiting_replacements.setdefault(intent.client_id, []).append(intent)
                        continue
                    else:
                        reasons = self.result.blocked_reasons
                        reasons["replace_missed"] = reasons.get("replace_missed", 0) + 1

    def _absorb_outcomes(self, outcomes: list) -> bool:
        """Fold in what the venue did, and keep the OMS honest about it.

        THE SAME THREE STEPS ``Runner._place`` DOES, IN THE SAME ORDER. The loop used to do
        only the middle one, and the two it skipped are the two that keep the OMS's view of
        "what is still live" equal to the book's:

          1. Self-trade prevention pulled some of our resting orders off the book. Left
             untold, the OMS reports them live forever, ``exposure_ticks`` counts them
             against the gross limit permanently, and ``MarketMaker._reconcile`` believes a
             quote it no longer has. ``matching.py`` says a two-sided maker WILL cross itself,
             so this is the steady state, not an edge case.
          2. Absorb the fills.
          3. An order that left no resting remainder is DONE. Left untold, it stays live
             forever and every strategy that checks its own open orders goes silent — a
             deadlock that reads as a strategy with no signal. In the unattended loop, with
             nobody watching, that silence is the whole failure.
        """
        for outcome in outcomes:
            if not self.result.accounting_valid:
                # advance() has already matched the entire batch. Preserve its remaining
                # fills, but do not acknowledge orders or run callbacks using a broken OMS.
                self.result.unreconciled_fills.extend(outcome.fills)
                continue
            # BUG-048: acceptance only puts an order on the simulated wire. The venue ACK is
            # the released outcome, so PENDING_NEW must survive until this point. Ack before
            # fills/cancellation so every later transition describes a venue-known order.
            order = self.oms.get(outcome.client_id)
            if order is not None and order.is_live:
                self.oms.on_acknowledged(outcome.client_id, ts=outcome.ts)
            for cancelled in outcome.result.stp_cancelled:
                order = self.oms.get(cancelled)
                if order is not None and order.is_live:
                    self.oms.on_cancelled(cancelled, ts=outcome.ts,
                                          reason="self_trade_prevention")
            # BUG-099: preserve the venue's outcome order. A released order can partially
            # fill, rest its remainder, and then be STP-cancelled by a later release in this
            # same advance() batch. The old global cancellation pass cancelled that remainder
            # before absorbing the earlier fill, producing CANCELLED -> PARTIALLY_FILLED.
            if not self._absorb(outcome.fills):
                continue

            order = self.oms.get(outcome.client_id)
            if order is not None and order.is_live and not outcome.rested:
                self.oms.on_cancelled(outcome.client_id, ts=outcome.ts,
                                      reason=outcome.result.status.value)
        return self.result.accounting_valid

    def _absorb(self, fills: list[Fill]) -> bool:
        for fill in fills:
            if not self.result.accounting_valid:
                self.result.unreconciled_fills.append(fill)
                continue
            order = self.oms.get(fill.order_id)
            if order is None:
                continue
            try:
                self.oms.on_fill(fill)
            except IllegalTransition as exc:
                # RW-22 / BUG-139: an overfill or a terminal-order fill means accounting
                # is incomplete. Keep the venue evidence and stop, including callbacks
                # later in this batch; a plausible partial PnL is not a valid run result.
                self.result.unreconciled_fills.append(fill)
                self._invalidate_accounting(f"invalid fill accounting: {exc}", fill=fill)
                continue
            self.result.fills.append(fill)
            self.pnl.on_fill(fill)
            ctx = self._context(fill.ingest_ts)
            for strategy in self.strategies:
                if strategy.name == order.strategy:
                    self._route(strategy, list(strategy.on_fill(fill, ctx)), fill.ingest_ts)
        return self.result.accounting_valid

    def _invalidate_accounting(self, reason: str, *, fill: Fill | None = None) -> None:
        self.result.desync_fills += int(fill is not None)
        self.result.accounting_valid = False
        self.result.halted = True
        self.result.halt_reason = reason
        self.result.attribution = None
        self.guards.trip(reason)
        payload = {
            "reason": reason, "at_event": self.result.events,
            "accounting_valid": False, "desync_fills": self.result.desync_fills,
        }
        if fill is not None:
            payload["fill"] = _fill_evidence(fill)
        self._audit(EVENT_QUANT_SIM_BREACH, payload)

    def _audit(self, event_type: str, payload: dict) -> None:
        """Write to the cockpit's hash-chained log when a sink is injected.

        Optional rather than required so the loop stays usable in a notebook, but the sink is
        what makes a session reconstructible after the fact. A simulated run that left no
        trace would be the only activity in this repo that did not.
        """
        if self.audit_sink is not None:
            self.audit_sink(event_type, payload)


def _fill_evidence(fill: Fill) -> dict:
    """JSON-native venue evidence for the audited failure and final session record."""
    return {**asdict(fill), "side": fill.side.value, "liquidity": fill.liquidity.value}


def _report_fills(report: VenueReport | list[Fill]) -> list[Fill]:
    if isinstance(report, list):
        return report
    return report.fills if isinstance(report, VenueOutcome) else []


def total_from_attribution(attribution: dict | None) -> Decimal:
    """Read the net total out of a serialised attribution. Convenience for callers."""
    if not attribution:
        return Decimal("0")
    return Decimal(attribution.get("total", "0"))

"""QT-07 — the simulated venue: a matching engine with a latency model.

WHAT IT IS
-----------
A ``LimitOrderBook`` plus the two delays that make an order's fate depend on time rather than
only on price:

    ENTRY LATENCY      our order is not live at the instant we decide to send it. It is in
                       flight, and the book can move underneath it.
    MARKET DATA DELAY  the book we decided against is the book as it was, not as it is.

Both default to nonzero. A zero-latency venue is the single most flattering assumption
available to a high-frequency strategy — it can react to a price move at the instant of the
move and always join a queue before anyone who saw the same event — so zero is available
(``LatencyModel.instant()``) for unit tests and is never the default.

WHY ORDERS QUEUE INSTEAD OF APPLYING IMMEDIATELY
-------------------------------------------------
``accept_order`` puts the order in a pending queue stamped with its arrival time, and
``advance`` releases it when event time reaches that stamp. This is the whole mechanism, and
it produces the behaviour that matters: an order sent into a moving market can arrive to find
the level gone, the price through, or its intended queue position taken. A venue that applies
orders instantly cannot represent any of that, and a strategy tuned against one has been
tuned against a market that reacts to it with perfect obedience.

THE METHOD NAMES ARE CONSTRAINED
---------------------------------
``accept_order`` and ``cancel_order`` — never the broker-path verbs. The Invariant-1 CI gate
is a plain regex over source text, not an import graph, so any of those four names followed
by an open paren fails the build anywhere outside ``brokers/`` and ``tests/`` — even inside a
simulator that could not reach a broker if it tried, and even in a comment explaining the
rule. (That is not hypothetical: the first draft of this docstring tripped the gate.) See
``quant/__init__.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from quant.book.lob import LimitOrderBook
from quant.book.matching import AggressiveOrder, MatchResult, TimeInForce, match
from quant.events import Event, Fill
from quant.oms import NewOrder

_TIF = {"day": TimeInForce.DAY, "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC, "fok": TimeInForce.FOK}


@dataclass(frozen=True, slots=True)
class LatencyModel:
    """The two delays. Both in event-time nanoseconds.

    Defaults are order-of-magnitude plausible for a colocated participant on US equities:
    tens of microseconds each way. They are PARAMETERS — a strategy whose profitability
    depends sensitively on them has discovered a latency arbitrage, not an edge, and should
    be reported as such rather than as a return.
    """

    order_entry_ns: int = 50_000
    market_data_ns: int = 30_000
    cancel_ns: int = 40_000

    @classmethod
    def instant(cls) -> "LatencyModel":
        """A zero-latency venue. FOR UNIT TESTS ONLY — never a default.

        It is the most flattering assumption available to a fast strategy: react at the
        instant of a move, always win the queue. Any headline number produced against this
        should be treated as an upper bound and labelled one.
        """
        return cls(order_entry_ns=0, market_data_ns=0, cancel_ns=0)

    @property
    def is_instant(self) -> bool:
        return self.order_entry_ns == 0 and self.market_data_ns == 0 and self.cancel_ns == 0


@dataclass(slots=True)
class _Pending:
    arrive_ts: int
    intent: NewOrder
    owner: str
    seq: int


@dataclass(frozen=True, slots=True)
class VenueOutcome:
    """What the venue did with one released order.

    Carries the ``client_id`` alongside the ``MatchResult`` because the result alone cannot
    identify the order that produced it: ``resting_order_id`` is None precisely in the case a
    caller most needs to detect — an aggressive order that fully traded or was killed and so
    never rested at all.
    """

    client_id: str
    result: MatchResult
    ts: int

    @property
    def fills(self) -> list[Fill]:
        return self.result.fills

    @property
    def rested(self) -> bool:
        return self.result.resting_order_id is not None


@dataclass(slots=True)
class SimVenue:
    """An in-process matching venue. NOT a broker, and structurally unable to become one.

    Holds no network handle, no credential, and no reference to anything in ``brokers/``.
    ``AutoLoop`` type-asserts against this class, and the CI import gate makes the absence of
    a broker path a property of the package rather than of this docstring.
    """

    symbol: str
    latency: LatencyModel = field(default_factory=LatencyModel)
    book: LimitOrderBook = field(init=False)
    _pending: list[_Pending] = field(default_factory=list, init=False, repr=False)
    _seq: int = field(default=0, init=False, repr=False)
    accepted: int = field(default=0, init=False)
    cancelled: int = field(default=0, init=False)
    expired_in_flight: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.book = LimitOrderBook(self.symbol)

    # ---- our order flow ---------------------------------------------------------------

    def accept_order(self, intent: NewOrder, *, owner: str, ts: int) -> int:
        """Take an order INTO FLIGHT. It is not live yet; ``advance`` makes it live.

        Returns the arrival timestamp so a caller can reason about the gap. Deliberately does
        not return a fill — at the moment of sending, whether it fills is not yet knowable,
        and a signature that returned one would invite treating it as though it were.
        """
        self._seq += 1
        arrive = ts + self.latency.order_entry_ns
        self._pending.append(_Pending(arrive_ts=arrive, intent=intent, owner=owner, seq=self._seq))
        return arrive

    def cancel_order(self, client_id: str, *, ts: int = 0) -> bool:
        """Withdraw a resting order, or drop one still in flight.

        Cancelling an in-flight order succeeds here without modelling the race in which the
        cancel arrives after the order has already traded. That race is real; representing it
        properly needs a cancel to travel with its own latency and lose, which is a refinement
        worth making before any result from this venue is treated as a latency measurement.
        """
        if client_id in self.book:
            self.book.cancel(client_id)
            self.cancelled += 1
            return True
        for index, pending in enumerate(self._pending):
            if pending.intent.client_id == client_id:
                self._pending.pop(index)
                self.expired_in_flight += 1
                return True
        return False

    def advance(self, ts: int) -> list[VenueOutcome]:
        """Release every in-flight order that has arrived by ``ts``, matching each one.

        Orders are released in ARRIVAL order, then by send order for ties — the venue sees
        them in the order they land on the wire, and a stable tiebreak keeps a replay
        deterministic when two orders arrive in the same nanosecond.

        Returns the FULL outcome per released order, not just its fills. It used to return
        only ``result.fills``, which threw away the two facts a caller needs to keep its OMS
        honest: whether the order left a resting remainder, and which of the caller's other
        orders self-trade prevention just pulled off the book. ``Runner._place`` has the book
        object in hand and reads them directly; ``AutoLoop`` goes through this venue and so
        could not, which is exactly how it ended up stranding orders.
        """
        if not self._pending:
            return []
        ready = [p for p in self._pending if p.arrive_ts <= ts]
        if not ready:
            return []
        self._pending = [p for p in self._pending if p.arrive_ts > ts]
        ready.sort(key=lambda p: (p.arrive_ts, p.seq))
        return [self._apply(pending, ts) for pending in ready]

    def _apply(self, pending: _Pending, ts: int) -> VenueOutcome:
        intent = pending.intent
        result: MatchResult = match(
            self.book,
            AggressiveOrder(order_id=intent.client_id, side=intent.side, qty=intent.qty,
                            price_ticks=intent.price_ticks,
                            tif=_TIF.get(intent.tif, TimeInForce.DAY), owner=pending.owner),
            seq=pending.seq, exchange_ts=pending.arrive_ts,
            recv_ts=pending.arrive_ts + self.latency.market_data_ns,
            ingest_ts=max(ts, pending.arrive_ts + self.latency.market_data_ns),
        )
        self.accepted += 1
        return VenueOutcome(client_id=intent.client_id, result=result,
                            ts=max(ts, pending.arrive_ts + self.latency.market_data_ns))

    # ---- market flow -------------------------------------------------------------------

    def apply_market_event(self, event: Event) -> list[Fill]:
        """Fold a feed event into the venue's book, returning any fills of ours.

        Delegates to ``runner.apply_market_event`` so replay and simulated execution cannot
        drift in what an event MEANS. Two definitions of "a trade happened" is how a backtest
        and a paper run end up disagreeing for reasons nobody can locate.
        """
        from quant.runner import apply_market_event as _apply

        return _apply(self.book, event)

    # ---- reads ---------------------------------------------------------------------------

    def visible_book(self, ts: int, levels: int = 5):
        """The book AS THE STRATEGY SEES IT — stamped back by the market-data delay.

        The stamp is what makes the staleness visible downstream; the contents are current,
        because reconstructing a delayed book properly needs a history of snapshots. That is
        a known simplification and it is stated here rather than discovered later: with a
        nonzero ``market_data_ns`` this venue models entry latency honestly and market-data
        latency only in the timestamp.
        """
        return self.book.snapshot(levels=levels, ts=max(0, ts - self.latency.market_data_ns))

    @property
    def in_flight(self) -> int:
        return len(self._pending)

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

from collections import deque
from dataclasses import dataclass, field, replace

from quant.book.lob import BookSnapshot, LimitOrderBook
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
    # (apply_ts, seq, client_id) — cancels IN FLIGHT (BUG-090). Applied by advance() in
    # arrival order interleaved with order releases, so a cancel can genuinely LOSE the
    # withdraw-vs-fill race instead of always winning it instantly.
    _pending_cancels: list[tuple[int, int, str]] = field(default_factory=list, init=False,
                                                         repr=False)
    # Bounded book history for the DELAYED view (BUG-079); populated only when
    # market_data_ns is nonzero.
    _history: deque = field(default_factory=lambda: deque(maxlen=8192), init=False,
                            repr=False)
    _seq: int = field(default=0, init=False, repr=False)
    accepted: int = field(default=0, init=False)
    cancelled: int = field(default=0, init=False)
    expired_in_flight: int = field(default=0, init=False)
    cancel_misses: int = field(default=0, init=False)
    # Cancels the venue CONFIRMED since the last drain (BUG-139). A traveling cancel is
    # only a request until it arrives; the loop marks its OMS order cancelled when it
    # drains the confirmation, so a fill that lands first is a legitimate fill of a
    # live order — not a crash and not a silently dropped fill.
    _confirmed_cancels: list[str] = field(default_factory=list, init=False, repr=False)

    _HISTORY_LEVELS = 10

    def __post_init__(self) -> None:
        self.book = LimitOrderBook(self.symbol)

    def _record_state(self, ts: int | None = None) -> None:
        """Capture the post-mutation book for the delayed view (BUG-079)."""
        if self.latency.market_data_ns:
            self._history.append(self.book.snapshot(levels=self._HISTORY_LEVELS, ts=ts))

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

        BUG-090: ``cancel_ns`` was declared, read by ``is_instant``, and never applied —
        every cancel landed instantly, so a requote-heavy strategy always won the
        withdraw-vs-fill race for free. With a nonzero ``cancel_ns`` and a real ``ts``, the
        cancel now TRAVELS: it is queued and applied by ``advance`` in arrival order, where
        it can genuinely arrive after the order has traded (a miss, counted). ``ts=0``
        (the pre-existing instant path used by direct/unit callers) keeps the old
        semantics, as does an instant latency model.

        A traveling cancel for an id the venue holds nowhere stays "accepted for
        transmission" and counts a miss at arrival (pinned by
        test_cancel_that_arrives_too_late_is_a_counted_miss) — the arrival-time
        accounting, not the send-time return, is the miss signal.
        """
        if self.latency.cancel_ns and ts:
            self._seq += 1
            self._pending_cancels.append((ts + self.latency.cancel_ns, self._seq, client_id))
            return True                     # accepted for transmission; it may lose the race
        return self._cancel_now(client_id)

    def _cancel_now(self, client_id: str) -> bool:
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

    def replace_order(self, client_id: str, *, new_qty: int | None = None,
                      new_price_ticks: int | None = None, ts: int = 0) -> bool:
        """Apply a reprice/resize to a RESTING order — or to one still IN FLIGHT.

        BUG-078: ``AutoLoop`` only replaced orders already in the book, so a reprice of an
        order still travelling (accepted, not yet released by ``advance``) was silently
        dropped: the order later landed and filled at the OLD terms the strategy believed
        it had replaced. An in-flight replace rewrites the pending intent itself. Returns
        False when the order exists nowhere (e.g. it just fully filled) so the caller can
        count the miss instead of swallowing it.
        """
        if client_id in self.book:
            self.book.replace(client_id, new_price_ticks=new_price_ticks,
                              new_qty=new_qty, ts=ts)
            return True
        for pending in self._pending:
            if pending.intent.client_id == client_id:
                # BUG-139: the resting branch validates; the pending branch did not — a
                # reprice-to-zero staged a poisoned intent that crashed advance() with
                # "aggressive order must have positive qty". Refuse it here so the
                # caller counts a miss instead of killing the run.
                if new_qty is not None and new_qty <= 0:
                    return False
                if new_price_ticks is not None and new_price_ticks <= 0:
                    return False
                pending.intent = replace(
                    pending.intent,
                    qty=new_qty if new_qty is not None else pending.intent.qty,
                    price_ticks=(new_price_ticks if new_price_ticks is not None
                                 else pending.intent.price_ticks),
                )
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
        ready = [p for p in self._pending if p.arrive_ts <= ts]
        due_cancels = [c for c in self._pending_cancels if c[0] <= ts]
        if not ready and not due_cancels:
            return []
        self._pending_cancels = [c for c in self._pending_cancels if c[0] > ts]
        # One arrival stream: orders and cancels land in wire order (BUG-090), so a cancel
        # can arrive after the order it targets has already traded — and miss.
        actions: list[tuple[int, int, object]] = [(p.arrive_ts, p.seq, p) for p in ready]
        actions += [(apply_ts, seq, client_id) for apply_ts, seq, client_id in due_cancels]
        actions.sort(key=lambda a: (a[0], a[1]))
        outcomes: list[VenueOutcome] = []
        for arrive, _seq, payload in actions:
            if isinstance(payload, _Pending):
                # BUG-098: do not remove every ready order before replaying this merged
                # arrival stream. An earlier cancel must still be able to find and expire an
                # order whose release is later in the same advance() batch. If it did, the
                # snapshotted release action is now stale and must not resurrect the order.
                if not any(p is payload for p in self._pending):
                    continue
                self._pending.remove(payload)
                outcomes.append(self._apply(payload, ts))
            else:
                if self._cancel_now(payload):
                    # BUG-139: the cancel genuinely arrived and withdrew a live order —
                    # confirm it so the loop can retire its OMS view. A miss (already
                    # traded away) is counted, as before.
                    self._confirmed_cancels.append(payload)
                else:
                    self.cancel_misses += 1     # the race, lost: it filled before the cancel
            self._record_state(arrive)          # BUG-079: history at each mutation instant
        return outcomes

    def drain_confirmed_cancels(self) -> list[str]:
        """Take-and-clear the cancel confirmations since the last drain (BUG-139).

        The loop calls this after every ``advance`` and retires its OMS view of each
        confirmed id. Confirmations are venue truth — the cancel ARRIVED — so they are
        the only thing that may move a live simulated order to cancelled.
        """
        confirmed, self._confirmed_cancels = self._confirmed_cancels, []
        return confirmed

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

        fills = _apply(self.book, event)
        self._record_state(getattr(event, "exchange_ts", None))   # BUG-079
        return fills

    # ---- reads ---------------------------------------------------------------------------

    def visible_book(self, ts: int, levels: int = 5):
        """The book AS THE STRATEGY SEES IT — genuinely delayed by ``market_data_ns``.

        BUG-079: this used to back-date only the snapshot's ``ts`` while returning CURRENT
        contents — and no strategy reads ``book.ts``, so a configured data latency changed
        nothing: the maker still reacted to a move at the instant of the move, the exact
        zero-latency flattery the LatencyModel documents itself as avoiding. The venue now
        keeps a bounded post-mutation snapshot history and serves the newest state at or
        before ``ts - market_data_ns``. Before any data is old enough to have arrived, the
        strategy sees an EMPTY book — it has received nothing yet, which is the truth.
        """
        delay = self.latency.market_data_ns
        if not delay:
            return self.book.snapshot(levels=levels, ts=ts)
        cutoff = ts - delay
        for snap in reversed(self._history):
            if snap.ts is not None and snap.ts <= cutoff:
                return BookSnapshot(symbol=snap.symbol, seq=snap.seq, ts=snap.ts,
                                    bids=snap.bids[:levels], asks=snap.asks[:levels])
        return BookSnapshot(symbol=self.symbol, seq=0, ts=max(0, cutoff), bids=(), asks=())

    @property
    def in_flight(self) -> int:
        return len(self._pending)

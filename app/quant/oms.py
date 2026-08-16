"""QT-02 — order management: intents in, lifecycle state out. The strategy/venue seam.

WHAT THIS OWNS AND WHAT IT REFUSES TO OWN
------------------------------------------
Strategies never touch the book. They emit INTENTS ("I want to be bid 200 at 100"), and the
OMS is what turns intents into venue actions, tracks what is actually live, and folds acks
and fills back into position and state. That indirection buys three things:

  * A strategy is testable as a pure function of (market state -> intents). No book, no
    venue, no clock in a strategy unit test.
  * There is ONE place that knows what is genuinely resting, so a strategy cannot double-send
    a cancel or reprice an order that already filled. Both are the kind of race that produces
    a phantom position in a live system and an unreproducible backtest in a simulated one.
  * Position is derived from FILLS ONLY, never from intents. An intent is a wish; a fill is
    a fact. Every "my position drifted" bug in a trading system traces back to something that
    updated inventory on the wish.

DELIBERATELY MIRRORS ``brokers/order_state_machine.py``, SHARES NOTHING WITH IT
--------------------------------------------------------------------------------
The state names and the adjacency rules here are the same shape as the real order state
machine, so a reader moving between them is not learning two vocabularies for one concept.
They are nonetheless entirely separate code with NO import between them, because they carry
completely different authority: that one governs orders that reach a broker and is guarded
by capability tokens, HMAC-signed adjudication and a runtime tracer; this one governs
simulated orders and is guarded by nothing, because it needs to be guarded by nothing.

Do not be tempted to unify them. The similarity is a readability convenience; the separation
is the containment argument (see ``quant/__init__.py``).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from quant.events import Fill, Side


class OrderState(enum.Enum):
    """Where an order is in its life. Mirrors the real machine's vocabulary.

    ``PENDING_NEW`` exists because in a latency-modelled simulation an order is NOT live the
    instant a strategy asks for it — it is in flight. A strategy that assumes otherwise has
    assumed away exactly the latency the engine exists to measure.
    """

    PENDING_NEW = "pending_new"
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


TERMINAL_STATES = frozenset({OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED})

_LEGAL: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING_NEW: frozenset({
        OrderState.WORKING, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
        OrderState.CANCELLED, OrderState.REJECTED,
    }),
    OrderState.WORKING: frozenset({
        OrderState.PARTIALLY_FILLED, OrderState.FILLED,
        OrderState.CANCELLED, OrderState.REJECTED,
    }),
    OrderState.PARTIALLY_FILLED: frozenset({
        OrderState.FILLED, OrderState.CANCELLED, OrderState.PARTIALLY_FILLED,
    }),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
}


class IllegalTransition(RuntimeError):
    """An order was moved between states that do not connect.

    Raised, never logged-and-continued. An order that went from FILLED back to WORKING means
    the fill stream and the ack stream disagree about reality, and every position derived
    afterwards is wrong. In a simulator this is always our own bug, so it must be loud.
    """


def legal_transition(src: OrderState, dst: OrderState) -> bool:
    return dst in _LEGAL[src]


@dataclass(slots=True)
class ManagedOrder:
    """One order the OMS believes in, with its running fill accounting."""

    client_id: str
    strategy: str
    symbol: str
    side: Side
    qty: int
    price_ticks: int | None = None
    state: OrderState = OrderState.PENDING_NEW
    filled_qty: int = 0
    fill_notional_ticks: int = 0
    created_ts: int = 0
    updated_ts: int = 0
    reason: str | None = None

    @property
    def remaining_qty(self) -> int:
        return self.qty - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_live(self) -> bool:
        """Live means the venue could still fill it — including while in flight.

        ``PENDING_NEW`` counts as live on purpose. Treating an in-flight order as not-live is
        how a strategy sends a duplicate: it looks at its open orders, sees nothing, and
        quotes again. Under any nonzero latency that produces two resting orders where the
        strategy intended one, and the resulting inventory blowout looks like a strategy
        failure rather than a bookkeeping one.
        """
        return not self.is_terminal

    @property
    def avg_fill_ticks(self) -> float | None:
        if not self.filled_qty:
            return None
        return self.fill_notional_ticks / self.filled_qty


@dataclass(frozen=True, slots=True, kw_only=True)
class NewOrder:
    """A strategy's request to place an order. ``price_ticks=None`` means market."""

    client_id: str
    symbol: str
    side: Side
    qty: int
    price_ticks: int | None = None
    tif: str = "day"


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelOrder:
    client_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ReplaceOrder:
    """Reprice/resize. ALWAYS loses queue priority at the book — see ``lob.replace``."""

    client_id: str
    new_price_ticks: int | None = None
    new_qty: int | None = None


Intent = NewOrder | CancelOrder | ReplaceOrder


class OMS:
    """Order lifecycle and position, derived from acks and fills.

    Holds no venue reference. The runner takes intents from here and applies them; results
    come back through ``on_ack``/``on_fill``/``on_cancelled``. That direction of control is
    what keeps the OMS usable identically for replay, sim execution and post-hoc analysis.
    """

    __slots__ = ("_orders", "_positions", "_by_strategy", "_live")

    def __init__(self) -> None:
        self._orders: dict[str, ManagedOrder] = {}
        self._positions: dict[str, int] = {}
        # dict-as-ordered-set, NOT set. Iteration order over a set[str] depends on string
        # hash randomization, so it differs between processes unless PYTHONHASHSEED is
        # pinned. These collections are iterated to build the order list a strategy sees, so
        # a set would make the engine's headline property — two runs of the same seed
        # produce byte-identical output — quietly false, and false only across processes,
        # which is the hardest way to notice.
        self._by_strategy: dict[str, dict[str, None]] = {}
        self._live: dict[str, None] = {}
        """Client ids currently live, maintained incrementally.

        WHY AN INDEX AND NOT A SCAN: ``live_orders`` used to filter every order ever
        created. Terminal orders are never forgotten, so the scan grows with the run and the
        total cost is quadratic in order count — 12.8M ``is_live`` checks for a 20,000-event
        simulation, ~85% of total runtime. The index makes the read O(live) instead of
        O(ever).

        The index is only safe because there are exactly TWO writers: ``register`` (which
        creates orders live) and ``_transition`` (the only assignment to ``order.state``
        anywhere). ``check_integrity`` proves it has not drifted, and a randomized
        differential test asserts it against a full scan.
        """

    # ---- intake ----------------------------------------------------------------------

    def register(self, intent: NewOrder, *, strategy: str, ts: int = 0) -> ManagedOrder:
        """Record an intent as an in-flight order. Does NOT touch a venue.

        Rejects a duplicate client id while the previous order is still live. Reusing an id
        for a live order makes cancels ambiguous, and an ambiguous cancel in a simulator is
        a silently wrong fill sequence rather than an error.
        """
        existing = self._orders.get(intent.client_id)
        if existing is not None and existing.is_live:
            raise ValueError(
                f"client_id {intent.client_id!r} is already live in state {existing.state.value}; "
                "reusing an id for a live order makes cancel/replace ambiguous"
            )
        order = ManagedOrder(
            client_id=intent.client_id, strategy=strategy, symbol=intent.symbol,
            side=intent.side, qty=intent.qty, price_ticks=intent.price_ticks,
            created_ts=ts, updated_ts=ts,
        )
        self._orders[intent.client_id] = order
        self._by_strategy.setdefault(strategy, {})[intent.client_id] = None
        self._live[intent.client_id] = None
        return order

    def _transition(self, order: ManagedOrder, dst: OrderState, *, ts: int, reason: str | None = None) -> None:
        if not legal_transition(order.state, dst):
            raise IllegalTransition(
                f"order {order.client_id!r}: {order.state.value} -> {dst.value} is not a legal "
                "transition; the ack and fill streams disagree about reality"
            )
        order.state = dst
        order.updated_ts = ts
        if reason is not None:
            order.reason = reason
        # THE ONLY place an order leaves the live index, because this is the only place a
        # state is assigned. Keeping the two together is what makes the index trustworthy;
        # any future transition path that bypasses this function would silently leak.
        if dst in TERMINAL_STATES:
            self._live.pop(order.client_id, None)

    # ---- venue callbacks ---------------------------------------------------------------

    def on_acknowledged(self, client_id: str, *, ts: int = 0) -> ManagedOrder:
        order = self._require(client_id)
        if order.state is OrderState.PENDING_NEW:
            self._transition(order, OrderState.WORKING, ts=ts)
        return order

    def on_rejected(self, client_id: str, *, reason: str, ts: int = 0) -> ManagedOrder:
        order = self._require(client_id)
        self._transition(order, OrderState.REJECTED, ts=ts, reason=reason)
        return order

    def on_cancelled(self, client_id: str, *, ts: int = 0, reason: str | None = None) -> ManagedOrder:
        order = self._require(client_id)
        self._transition(order, OrderState.CANCELLED, ts=ts, reason=reason)
        return order

    def on_fill(self, fill: Fill) -> ManagedOrder:
        """Fold a fill into the order and the position. THE ONLY THING THAT MOVES POSITION.

        Overfill is refused rather than clamped. A fill that exceeds the order's remaining
        size means the venue and the OMS disagree about what was live; clamping would paper
        over the disagreement and leave the position quietly short of reality — the exact
        failure that makes a live desync undetectable until reconciliation.
        """
        order = self._require(fill.order_id)
        if fill.qty > order.remaining_qty:
            raise IllegalTransition(
                f"fill of {fill.qty} exceeds {order.remaining_qty} remaining on "
                f"{fill.order_id!r}: the venue and the OMS disagree about what was live"
            )
        order.filled_qty += fill.qty
        order.fill_notional_ticks += fill.price_ticks * fill.qty
        self._positions[order.symbol] = self._positions.get(order.symbol, 0) + fill.signed_qty
        target = OrderState.FILLED if order.remaining_qty == 0 else OrderState.PARTIALLY_FILLED
        self._transition(order, target, ts=fill.ingest_ts)
        return order

    def _require(self, client_id: str) -> ManagedOrder:
        order = self._orders.get(client_id)
        if order is None:
            raise KeyError(f"unknown client_id {client_id!r}: no such order in the OMS")
        return order

    # ---- reads ---------------------------------------------------------------------------

    def get(self, client_id: str) -> ManagedOrder | None:
        return self._orders.get(client_id)

    def position(self, symbol: str) -> int:
        """Signed inventory in shares. Derived from fills only — never from intents."""
        return self._positions.get(symbol, 0)

    def positions(self) -> dict[str, int]:
        return {s: q for s, q in self._positions.items() if q}

    def live_orders(self, *, symbol: str | None = None, strategy: str | None = None) -> list[ManagedOrder]:
        """Orders the venue could still fill. O(live), not O(every order ever created).

        Iterates the live INDEX rather than filtering all orders — see ``_live``. Ordering is
        registration order and is deterministic, which strategies depend on: two runs of the
        same seed must produce the same order list, or the engine's reproducibility claim is
        only true within a single process.
        """
        owned: dict[str, None] | None = None
        if strategy is not None:
            owned = self._by_strategy.get(strategy)
            if not owned:
                return []

        orders = self._orders
        out: list[ManagedOrder] = []
        for cid in self._live:
            # Walk the live set and test membership, so cost tracks LIVE orders rather than
            # everything the strategy has ever sent.
            if owned is not None and cid not in owned:
                continue
            order = orders[cid]
            if symbol is None or order.symbol == symbol:
                out.append(order)
        return out

    def check_integrity(self) -> None:
        """Assert the live index agrees with a full scan. Raises on drift.

        The index is a cache of a derivable fact, and a stale cache here would mean a
        strategy silently stops seeing one of its own working orders — which presents as the
        strategy quoting a duplicate, not as a bookkeeping error. Cheap enough to call in
        tests and after a run; deliberately not called per event, which would reintroduce
        the O(n) scan it exists to remove.
        """
        scanned = {cid for cid, o in self._orders.items() if o.is_live}
        indexed = set(self._live)
        if scanned != indexed:
            raise AssertionError(
                f"OMS live index drifted: missing {sorted(scanned - indexed)}, "
                f"stale {sorted(indexed - scanned)}"
            )

    def exposure_ticks(self, symbol: str | None = None) -> int:
        """Absolute working notional in ticks*shares, including in-flight orders.

        In-flight orders count because the risk they represent is already committed — by the
        time you learn an order was rejected, the exposure either happened or did not, and a
        limit that only counts acknowledged orders is a limit you can exceed by sending fast.
        """
        return sum(
            abs((o.price_ticks or 0) * o.remaining_qty)
            for o in self.live_orders(symbol=symbol)
        )

    def all_orders(self) -> list[ManagedOrder]:
        return list(self._orders.values())


@dataclass(slots=True)
class OrderIdSequence:
    """Deterministic client-order-id generator: ``<strategy>-<n>``.

    Deterministic on purpose. UUIDs would make two runs of the same replay produce different
    ids, which breaks diffing one backtest against another — and diffing two runs is the main
    way a change's effect is actually measured.
    """

    prefix: str
    _counter: int = field(default=0, repr=False)

    def next(self) -> str:
        self._counter += 1
        return f"{self.prefix}-{self._counter}"

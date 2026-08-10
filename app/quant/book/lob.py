"""QT-01 — price-level limit order book with per-order queue position.

WHY THIS EXISTS AT ALL (the thing being replaced)
--------------------------------------------------
The usual toy market-making simulator fills a resting quote with probability ``exp(-k*delta)``
— an exponential in the distance from the mid. It is analytically convenient and it is
wrong in the one way that matters: it makes fill probability a function of PRICE ALONE. Two
orders at the same price fill identically regardless of whether one is at the front of the
queue and the other is 4,000 shares back.

That is not a detail. For a passive strategy the queue IS the business:

  * Being at the front means you trade often at the touch. Being at the back means you trade
    only when the level is swept — i.e. almost exclusively when the price is about to move
    THROUGH you. Same price, same spread, opposite selection. One earns the spread; the
    other is adversely selected into every move.
  * It changes what the optimal quote IS. Under an exponential model, quoting one tick wider
    always costs fill rate. With a real queue, quoting the touch early can dominate quoting
    inside late, because priority is an asset that accrues while you wait.
  * It is why "improve the price by a tick to get filled" is often a losing reflex: you paid
    a tick AND went to the back of a new queue.

No strategy parameter tweak recovers this. So the queue is modelled first, before any
strategy exists, and every fill in this engine comes from ``matching.py`` walking a real
book — never from a probability draw.

THE DATA STRUCTURE, AND WHY THIS ONE
-------------------------------------
Each price level holds ``dict[order_id, RestingOrder]``. Python dicts have preserved
insertion order as a language guarantee since 3.7, which gives all three properties a book
needs at once, and no other stdlib structure gives more than two:

  * FIFO iteration order for free -> price-time priority is the natural traversal;
  * O(1) removal by id -> cancels are cheap, and a real book is mostly cancels (well over
    90% of ITCH messages on a liquid name are adds and cancels that never trade);
  * O(1) append -> new orders join the tail, which is exactly the priority rule.

A ``deque`` gives FIFO and O(1) append but O(n) removal by identity — wrong for a workload
that is mostly cancellation. A heap gives none of them. A linked list gives all three but
must be hand-maintained, and a corrupted book is a silent wrong answer rather than a crash.

Best bid/ask come from ``bisect``-maintained sorted lists of ACTIVE price ticks, not from a
lazy-deletion heap. Levels number in the tens-to-hundreds, so the O(n) ``insort`` on
level-creation is irrelevant, and the structure is always exactly consistent — a lazy heap
holds stale entries that every reader must remember to skip, which is a correctness burden
paid at every call site forever to save an insort that never showed up in a profile.

PRICES ARE INTEGER TICKS
-------------------------
Levels are dict keys and sort keys. With floats, ``100.10`` and ``100.09999999999999`` are
two levels: the queue silently splits in half, every order's position is understated, and
the resulting backtest is optimistic in precisely the dimension this module exists to
measure. Integers make that unrepresentable. ``events.PriceScale`` is the only converter.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Iterator

from quant.events import Side


class OrderNotFound(KeyError):
    """A cancel/replace/lookup named an order the book does not hold.

    Raised rather than ignored. In a replay, a cancel for an unknown order means the feed
    dropped the corresponding add — the book has silently diverged from the venue's, and
    every queue position computed after that point is wrong by an unknown amount. Swallowing
    it converts a detectable desync into a plausible-looking backtest. ``matching.py`` and
    the replay feed decide how to handle it; the book's job is to be unable to hide it.
    """


class BookIntegrityError(RuntimeError):
    """An internal invariant of the book broke (level totals, index consistency).

    Never expected. If this raises, the book's own accounting disagrees with itself and no
    downstream number is trustworthy — so it fails loudly instead of continuing.
    """


@dataclass(slots=True)
class RestingOrder:
    """One order resting on the book.

    MUTABLE, unlike the events in ``quant/events.py``, and that asymmetry is deliberate:
    events are a historical record and must never change, whereas a resting order's
    remaining quantity is state that the venue edits in place. Copying on every partial fill
    would make queue identity harder to reason about, not easier.

    ``owner`` is ``None`` for orders belonging to the rest of the market and a strategy id
    for ours. That single field is what lets one book hold both the reconstructed venue book
    and our own quotes, which is the only way queue position can mean anything — a book
    containing only our orders has no queue to be positioned in.
    """

    order_id: str
    side: Side
    price_ticks: int
    qty: int
    entry_seq: int
    entry_ts: int
    owner: str | None = None
    original_qty: int = 0
    qty_ahead_at_entry: int = 0

    def __post_init__(self) -> None:
        if self.qty <= 0:
            raise ValueError(f"resting order {self.order_id!r} must have positive qty, got {self.qty}")
        if not self.original_qty:
            self.original_qty = self.qty

    @property
    def is_ours(self) -> bool:
        return self.owner is not None

    @property
    def filled_qty(self) -> int:
        return self.original_qty - self.qty


@dataclass(slots=True)
class PriceLevel:
    """All orders resting at one price on one side, in arrival order.

    ``total_qty`` is maintained incrementally rather than summed on demand. Depth is read far
    more often than it is written (every strategy callback reads it; only book events write
    it), and an O(k) sum per read turns a hot path into a quadratic one. The randomized
    conservation test exists specifically to prove the incremental total never drifts from
    the true sum — an incrementally-maintained aggregate that nobody checks is a classic
    source of slow, silent corruption.
    """

    price_ticks: int
    orders: dict[str, RestingOrder] = field(default_factory=dict)
    total_qty: int = 0

    def __len__(self) -> int:
        return len(self.orders)

    def __iter__(self) -> Iterator[RestingOrder]:
        return iter(self.orders.values())

    @property
    def is_empty(self) -> bool:
        return not self.orders

    def append(self, order: RestingOrder) -> None:
        """Join the back of the queue. The only way to enter a level."""
        if order.order_id in self.orders:
            raise ValueError(f"duplicate order_id {order.order_id!r} at level {self.price_ticks}")
        self.orders[order.order_id] = order
        self.total_qty += order.qty

    def remove(self, order_id: str) -> RestingOrder:
        order = self.orders.pop(order_id, None)
        if order is None:
            raise OrderNotFound(order_id)
        self.total_qty -= order.qty
        return order

    def reduce(self, order_id: str, qty: int) -> RestingOrder:
        """Take ``qty`` off an order IN PLACE, preserving its queue priority.

        This is what a partial fill and a partial cancel both do, and it is why they are one
        method: reducing size never costs priority on a real venue, while a cancel/replace
        pair does. Modelling a partial fill as remove-then-append would send our order to
        the back of the queue after every partial — an error that compounds silently and
        makes passive strategies look far worse than they are.
        """
        order = self.orders.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        if qty <= 0:
            raise ValueError(f"reduce qty must be positive, got {qty}")
        if qty > order.qty:
            raise ValueError(
                f"cannot reduce order {order_id!r} by {qty}: only {order.qty} remaining"
            )
        order.qty -= qty
        self.total_qty -= qty
        if order.qty == 0:
            del self.orders[order_id]
        return order

    def qty_ahead_of(self, order_id: str) -> int:
        """Total quantity that must trade before ``order_id`` does. THE number.

        Computed by traversal rather than from a cached counter. The cached alternative has
        to be adjusted on four different events (execute-ahead, cancel-ahead, partial-ahead,
        replace-ahead), each of which is a chance to drift, and drift here is invisible: the
        book still looks well-formed and every fill decision is quietly wrong. Levels hold
        tens of orders, so traversal costs nothing measurable, and it CANNOT be stale.
        """
        ahead = 0
        for candidate in self.orders.values():
            if candidate.order_id == order_id:
                return ahead
            ahead += candidate.qty
        raise OrderNotFound(order_id)

    def true_total(self) -> int:
        """Sum the level the slow way. Used only by the integrity check and tests."""
        return sum(o.qty for o in self.orders.values())


@dataclass(frozen=True, slots=True)
class LevelSnapshot:
    price_ticks: int
    qty: int
    order_count: int


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """An immutable read of the book at one instant.

    Exists because the WebSocket broadcast, ``verify.py`` and the tearsheet must all read
    book state without being able to mutate it, and without holding a reference that changes
    underneath them. Handing out the live ``LimitOrderBook`` would make every consumer a
    potential writer and make "the state at 10:31:04.100" unrepresentable.
    """

    symbol: str
    seq: int
    ts: int
    bids: tuple[LevelSnapshot, ...]
    asks: tuple[LevelSnapshot, ...]

    @property
    def best_bid_ticks(self) -> int | None:
        return self.bids[0].price_ticks if self.bids else None

    @property
    def best_ask_ticks(self) -> int | None:
        return self.asks[0].price_ticks if self.asks else None

    @property
    def spread_ticks(self) -> int | None:
        if not self.bids or not self.asks:
            return None
        return self.asks[0].price_ticks - self.bids[0].price_ticks

    @property
    def mid_ticks(self) -> float | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price_ticks + self.asks[0].price_ticks) / 2.0

    @property
    def microprice(self) -> float | None:
        """Size-weighted fair value. Weighted by the OPPOSITE side's size — see events.Quote."""
        if not self.bids or not self.asks:
            return None
        bid, ask = self.bids[0], self.asks[0]
        total = bid.qty + ask.qty
        if total <= 0:
            return self.mid_ticks
        return (bid.price_ticks * ask.qty + ask.price_ticks * bid.qty) / total

    @property
    def imbalance(self) -> float | None:
        """(bid - ask) / (bid + ask) at the touch, in [-1, 1]. Positive means bid-heavy."""
        if not self.bids or not self.asks:
            return None
        bid_qty, ask_qty = self.bids[0].qty, self.asks[0].qty
        total = bid_qty + ask_qty
        if total <= 0:
            return 0.0
        return (bid_qty - ask_qty) / total


class LimitOrderBook:
    """A single symbol's two-sided book with exact per-order queue positions.

    Holds STATE only. It does not decide what crosses, does not emit fills, and does not know
    what a strategy is — ``matching.py`` owns all of that. The separation is what lets the
    hand-built test sequences drive the book directly and assert on queue positions without
    a matcher, a feed, or a clock in the picture.
    """

    __slots__ = ("symbol", "_bids", "_asks", "_bid_prices", "_ask_prices", "_index", "_last_seq", "_last_ts")

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._bids: dict[int, PriceLevel] = {}
        self._asks: dict[int, PriceLevel] = {}
        # Both kept ASCENDING. Best bid is the last element, best ask the first — one sort
        # order for both sides means bisect is used identically and there is no reversed
        # comparator to get backwards.
        self._bid_prices: list[int] = []
        self._ask_prices: list[int] = []
        self._index: dict[str, RestingOrder] = {}
        self._last_seq = 0
        self._last_ts = 0

    # ---- internals -------------------------------------------------------------------

    def _side_maps(self, side: Side) -> tuple[dict[int, PriceLevel], list[int]]:
        if side is Side.BUY:
            return self._bids, self._bid_prices
        return self._asks, self._ask_prices

    def _level_for_write(self, side: Side, price_ticks: int) -> PriceLevel:
        levels, prices = self._side_maps(side)
        level = levels.get(price_ticks)
        if level is None:
            level = PriceLevel(price_ticks=price_ticks)
            levels[price_ticks] = level
            bisect.insort(prices, price_ticks)
        return level

    def _drop_level_if_empty(self, side: Side, price_ticks: int) -> None:
        levels, prices = self._side_maps(side)
        level = levels.get(price_ticks)
        if level is None or not level.is_empty:
            return
        del levels[price_ticks]
        position = bisect.bisect_left(prices, price_ticks)
        if position >= len(prices) or prices[position] != price_ticks:
            raise BookIntegrityError(
                f"price index desync: level {price_ticks} on {side.value} missing from the "
                "sorted index while its dict entry existed"
            )
        prices.pop(position)

    # ---- mutation --------------------------------------------------------------------

    def add(
        self,
        *,
        order_id: str,
        side: Side,
        price_ticks: int,
        qty: int,
        seq: int = 0,
        ts: int = 0,
        owner: str | None = None,
    ) -> RestingOrder:
        """Rest a new order at the BACK of its price level. The only entry point to the book.

        ``qty_ahead_at_entry`` is captured here and never recomputed. It is the denominator
        of every "how far have I advanced" question, and the answer to "what did I sign up
        for when I joined this queue" has to be the value at JOIN time — recomputing it later
        would silently rewrite history each time the level changed.
        """
        if order_id in self._index:
            raise ValueError(f"duplicate order_id {order_id!r} already resting in the book")
        level = self._level_for_write(side, price_ticks)
        order = RestingOrder(
            order_id=order_id,
            side=side,
            price_ticks=price_ticks,
            qty=qty,
            entry_seq=seq,
            entry_ts=ts,
            owner=owner,
            qty_ahead_at_entry=level.total_qty,
        )
        level.append(order)
        self._index[order_id] = order
        self._last_seq = max(self._last_seq, seq)
        self._last_ts = max(self._last_ts, ts)
        return order

    def cancel(self, order_id: str) -> RestingOrder:
        """Remove an order entirely. Raises ``OrderNotFound`` — see the class docstring."""
        order = self._index.pop(order_id, None)
        if order is None:
            raise OrderNotFound(order_id)
        levels, _ = self._side_maps(order.side)
        level = levels.get(order.price_ticks)
        if level is None:
            raise BookIntegrityError(
                f"order {order_id!r} indexed at {order.price_ticks} but that level is gone"
            )
        level.remove(order_id)
        self._drop_level_if_empty(order.side, order.price_ticks)
        return order

    def reduce(self, order_id: str, qty: int) -> RestingOrder:
        """Shrink an order in place, KEEPING its queue priority. Partial fill or partial cancel."""
        order = self._index.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        levels, _ = self._side_maps(order.side)
        level = levels[order.price_ticks]
        level.reduce(order_id, qty)
        if order.qty == 0:
            del self._index[order_id]
            self._drop_level_if_empty(order.side, order.price_ticks)
        return order

    def replace(
        self,
        order_id: str,
        *,
        new_price_ticks: int | None = None,
        new_qty: int | None = None,
        seq: int = 0,
        ts: int = 0,
    ) -> RestingOrder:
        """Cancel-and-new. LOSES QUEUE PRIORITY, ALWAYS. This is not a pessimisation.

        Real venues grant priority to (price, time-of-entry-at-that-price), and a replace
        establishes a new entry time. Modelling it as an in-place edit would hand a strategy
        free priority for repricing, which is the single most tempting and most wrong
        shortcut available here: it makes an aggressively-requoting market maker look
        profitable precisely because it never pays for the requote.

        A pure size DECREASE keeps priority on most venues, and ``reduce`` is that operation.
        This method is for anything else, and it always goes to the back.
        """
        existing = self._index.get(order_id)
        if existing is None:
            raise OrderNotFound(order_id)
        price = existing.price_ticks if new_price_ticks is None else new_price_ticks
        qty = existing.qty if new_qty is None else new_qty
        if qty <= 0:
            raise ValueError(f"replace qty must be positive, got {qty}")
        side, owner = existing.side, existing.owner
        self.cancel(order_id)
        return self.add(
            order_id=order_id, side=side, price_ticks=price, qty=qty,
            seq=seq, ts=ts, owner=owner,
        )

    # ---- reads -----------------------------------------------------------------------

    def get(self, order_id: str) -> RestingOrder | None:
        return self._index.get(order_id)

    def __contains__(self, order_id: object) -> bool:
        return order_id in self._index

    def __len__(self) -> int:
        return len(self._index)

    def queue_position(self, order_id: str) -> int:
        """Quantity that must trade before this order does. Zero means front of the queue.

        Measured in SHARES AHEAD, not orders ahead. Two orders ahead of you means nothing;
        two thousand shares ahead means you trade only after the level has largely turned
        over. Shares are what the matcher consumes, so shares are what the number counts.
        """
        order = self._index.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        levels, _ = self._side_maps(order.side)
        return levels[order.price_ticks].qty_ahead_of(order_id)

    def queue_progress(self, order_id: str) -> float:
        """Fraction of the original queue-ahead that has cleared, in [0, 1].

        1.0 means everything that was ahead at entry is gone (front of queue); 0.0 means no
        progress. An order that joined an EMPTY level starts at the front and is reported as
        1.0 rather than dividing by zero — it has nothing left to wait for, which is the same
        state, and returning 0.0 there would invert the meaning of the metric.
        """
        order = self._index.get(order_id)
        if order is None:
            raise OrderNotFound(order_id)
        if order.qty_ahead_at_entry <= 0:
            return 1.0
        remaining = self.queue_position(order_id)
        return max(0.0, min(1.0, 1.0 - remaining / order.qty_ahead_at_entry))

    def best_bid(self) -> int | None:
        return self._bid_prices[-1] if self._bid_prices else None

    def best_ask(self) -> int | None:
        return self._ask_prices[0] if self._ask_prices else None

    def best(self, side: Side) -> int | None:
        return self.best_bid() if side is Side.BUY else self.best_ask()

    @property
    def spread_ticks(self) -> int | None:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return ask - bid

    @property
    def mid_ticks(self) -> float | None:
        """Arithmetic mid. Float on purpose — a half-tick mid is a STATISTIC, not a price.

        Named identically to ``BookSnapshot.mid_ticks`` so a caller that holds either can ask
        the same question. Anything routed back to the book takes ``int`` ticks and will
        reject this value, which is the intended friction.
        """
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None
        return (bid + ask) / 2.0

    @property
    def is_crossed(self) -> bool:
        """True when the best bid is at or above the best ask.

        A crossed book is not necessarily corruption — it happens transiently in real feeds
        between the two halves of a price update, and across venues it is routine. The book
        REPRESENTS it rather than refusing it, and ``matching.py`` decides what to do,
        because a book that cannot represent a crossed state cannot replay a real feed.
        """
        bid, ask = self.best_bid(), self.best_ask()
        return bid is not None and ask is not None and bid >= ask

    def level_qty(self, side: Side, price_ticks: int) -> int:
        levels, _ = self._side_maps(side)
        level = levels.get(price_ticks)
        return level.total_qty if level is not None else 0

    def level(self, side: Side, price_ticks: int) -> PriceLevel | None:
        levels, _ = self._side_maps(side)
        return levels.get(price_ticks)

    def prices(self, side: Side) -> list[int]:
        """Active price ticks, BEST FIRST — descending for bids, ascending for asks.

        Returns best-first for both sides even though the internal lists are both ascending,
        because "walk the book from the touch outward" is what every caller wants and
        re-reversing it at each call site is how a matcher ends up walking the wrong way.
        """
        _, prices = self._side_maps(side)
        return list(reversed(prices)) if side is Side.BUY else list(prices)

    def depth(self, side: Side, levels: int = 5) -> list[LevelSnapshot]:
        maps, _ = self._side_maps(side)
        return [
            LevelSnapshot(price_ticks=p, qty=maps[p].total_qty, order_count=len(maps[p]))
            for p in self.prices(side)[:levels]
        ]

    def total_qty(self, side: Side) -> int:
        maps, _ = self._side_maps(side)
        return sum(level.total_qty for level in maps.values())

    def our_orders(self, owner: str | None = None) -> list[RestingOrder]:
        """Every resting order we own, optionally filtered to one strategy."""
        return [
            o for o in self._index.values()
            if o.is_ours and (owner is None or o.owner == owner)
        ]

    def snapshot(self, *, levels: int = 5, seq: int | None = None, ts: int | None = None) -> BookSnapshot:
        return BookSnapshot(
            symbol=self.symbol,
            seq=self._last_seq if seq is None else seq,
            ts=self._last_ts if ts is None else ts,
            bids=tuple(self.depth(Side.BUY, levels)),
            asks=tuple(self.depth(Side.SELL, levels)),
        )

    def check_integrity(self) -> None:
        """Assert the book's accounting agrees with itself. For tests and the randomized sweep.

        Verifies the three things that can drift independently: incremental level totals vs
        the true sum, the sorted price index vs the level dicts, and the order index vs the
        levels' contents. Any one of these drifting produces a book that still LOOKS
        well-formed while returning wrong queue positions — which is why the check is
        explicit and called from the randomized test rather than trusted.
        """
        seen: set[str] = set()
        for side in (Side.BUY, Side.SELL):
            levels, prices = self._side_maps(side)
            if sorted(prices) != prices:
                raise BookIntegrityError(f"{side.value} price index is not sorted: {prices}")
            if len(set(prices)) != len(prices):
                raise BookIntegrityError(f"{side.value} price index has duplicates: {prices}")
            if set(prices) != set(levels):
                raise BookIntegrityError(
                    f"{side.value} price index {sorted(prices)} disagrees with levels "
                    f"{sorted(levels)}"
                )
            for price, level in levels.items():
                if level.is_empty:
                    raise BookIntegrityError(f"empty level {price} on {side.value} was not dropped")
                if level.total_qty != level.true_total():
                    raise BookIntegrityError(
                        f"level {price} on {side.value}: cached total {level.total_qty} != "
                        f"true total {level.true_total()}"
                    )
                for order in level:
                    if order.price_ticks != price or order.side is not side:
                        raise BookIntegrityError(
                            f"order {order.order_id!r} filed at {side.value}/{price} but "
                            f"claims {order.side.value}/{order.price_ticks}"
                        )
                    if self._index.get(order.order_id) is not order:
                        raise BookIntegrityError(
                            f"order {order.order_id!r} in a level but not (or differently) indexed"
                        )
                    seen.add(order.order_id)
        if seen != set(self._index):
            raise BookIntegrityError(
                f"order index holds {sorted(set(self._index) - seen)} not present in any level"
            )

"""Python face of the Rust FIFO book.

The extension owns the queues. This module only adapts them to the dataclasses
and exception types ``quant.book.lob`` already exports, so callers — including
the existing tests — do not change.
"""
from __future__ import annotations

import weakref

import cockpit_quant
from quant.events import Liquidity, Side

_OrderNotFound: type | None = None
_Integrity: type | None = None
_BookSnapshot: type | None = None
_LevelSnapshot: type | None = None
_RestingOrder: type | None = None


def bind(order_not_found, integrity, book_snapshot, level_snapshot, resting_order):
    global _OrderNotFound, _Integrity, _BookSnapshot, _LevelSnapshot, _RestingOrder
    _OrderNotFound = order_not_found
    _Integrity = integrity
    _BookSnapshot = book_snapshot
    _LevelSnapshot = level_snapshot
    _RestingOrder = resting_order
    return LimitOrderBook, PriceLevel


def _side(name: str) -> Side:
    if name == "buy":
        return Side.BUY
    if name == "sell":
        return Side.SELL
    raise ValueError(f"unknown side {name!r}")


def _order(row):
    order_id, side, price, qty, entry_seq, entry_ts, owner, original_qty, ahead = row
    # ``RestingOrder`` refuses non-positive qty at construction. A full reduce
    # returns the same order with qty edited to 0 afterwards — that is legal,
    # and ``__post_init__`` does not run again. Mirror that here.
    order = _RestingOrder(
        order_id=order_id,
        side=_side(side),
        price_ticks=price,
        qty=qty if qty > 0 else 1,
        entry_seq=entry_seq,
        entry_ts=entry_ts,
        owner=owner,
        original_qty=original_qty,
        qty_ahead_at_entry=ahead,
    )
    if qty <= 0:
        order.qty = qty
    return order


def _raise(exc: BaseException):
    message = exc.args[0] if exc.args else str(exc)
    if isinstance(exc, cockpit_quant.NotFound):
        raise _OrderNotFound(message) from None
    if isinstance(exc, cockpit_quant.IntegrityError):
        raise _Integrity(message) from None
    if isinstance(exc, cockpit_quant.BookValueError):
        raise ValueError(message) from None
    raise exc


def _call(fn, *args):
    try:
        return fn(*args)
    except (cockpit_quant.NotFound, cockpit_quant.IntegrityError, cockpit_quant.BookValueError) as exc:
        _raise(exc)


class _Ladder:
    """Live proxy for ``_bid_prices`` / ``_ask_prices``. ``append`` is the corruption hook."""

    def __init__(self, core, side: str) -> None:
        self._core = core
        self._side = side

    def append(self, price: int) -> None:
        self._core.append_price(self._side, int(price))


def _cached(book, order_id: str):
    """The ``RestingOrder`` a caller still holds, or None once they dropped it."""
    ref = book._live.get(order_id)
    if ref is None:
        return None
    order = ref()
    if order is None:
        book._live.pop(order_id, None)
    return order


def _remember(book, order) -> None:
    order_id = order.order_id

    book_ref = weakref.ref(book)

    def _drop(ref, book_ref=book_ref, order_id=order_id):
        owner = book_ref()
        if owner is None:
            return
        current = owner._live.get(order_id)
        if current is ref:
            owner._live.pop(order_id, None)

    book._live[order_id] = weakref.ref(order, _drop)


def _take_row(book, row, *, fresh: bool = False, zero_leaves: bool = False):
    """Return the interned ``RestingOrder`` for ``row``.

    The Python book stores one mutable order and hands that same object out of
    ``add``, ``get``, ``reduce``, ``level().orders``, and ``our_orders``. A
    later ``reduce`` or fill edits ``qty`` on it. ``replace`` is cancel-plus-add,
    so the previous object keeps the qty it had and the book holds a new one.

    The cache is weak. A caller that dropped the object does not keep a stale
    copy, and a later ``get`` builds a new one from the book.
    """
    order_id, _side, _price, qty, *_rest = row
    existing = None if fresh else _cached(book, order_id)
    if existing is None:
        order = _order(row)
    else:
        order = existing
        order.qty = qty
    if zero_leaves and qty <= 0:
        book._live.pop(order_id, None)
    else:
        _remember(book, order)
    return order


def _absorb_batch(book, rows) -> list:
    """Apply a batch result to orders the caller already holds.

    ``"a"`` and ``"p"`` are new objects (replace is cancel-plus-add). ``"c"``
    drops the order and leaves its qty. ``"r"`` edits qty, including a full
    reduce to 0. ``"m"`` applies the matcher's qty updates and self-trade
    cancels. The qty-update list is not part of the public row.
    """
    public = []
    for row in rows:
        kind = row[0]
        if kind in ("a", "p"):
            # A new order. Forget the previous object without allocating one
            # the caller of the batch did not ask to hold.
            book._live.pop(row[1], None)
            public.append(row)
        elif kind == "c":
            book._live.pop(row[1], None)
            public.append(row)
        elif kind == "r":
            order = _cached(book, row[1])
            if order is not None:
                order.qty = row[4]
                if order.qty <= 0:
                    order.qty = 0
                    book._live.pop(row[1], None)
            public.append(row)
        elif kind == "m":
            # (kind, status, filled, remaining, trades, fills, resting, stp, touched)
            stp = row[7]
            touched = row[8] if len(row) > 8 else ()
            _apply_effects(book, touched, stp)
            public.append(row[:8])
        else:
            public.append(row)
    return public


def _apply_effects(book, touched, cancelled) -> None:
    """Update only the orders the matcher names.

    ``touched`` is ``(order_id, qty_after)`` for a trade. ``qty_after == 0`` is
    a full fill, and that is the only time a held object is zeroed. ``cancelled``
    is a self-trade removal: the held object keeps its qty, including when the
    remainder later rests under the same id. An id that is merely absent from
    the book is not treated as a fill.
    """
    cancelled_ids = set(cancelled)
    for order_id, qty in touched:
        if order_id in cancelled_ids:
            continue
        order = _cached(book, order_id)
        if order is None:
            continue
        order.qty = int(qty)
        if order.qty <= 0:
            order.qty = 0
            book._live.pop(order_id, None)
    for order_id in cancelled_ids:
        book._live.pop(order_id, None)


class PriceLevel:
    """Live view of one price. ``total_qty`` is writable so the integrity control can fail."""

    def __init__(self, book, side: Side, price: int) -> None:
        self._book = book
        self._core = book._core
        self._side = side
        self.price_ticks = price

    @property
    def total_qty(self) -> int:
        return self._core.level_qty(self._side.value, self.price_ticks)

    @total_qty.setter
    def total_qty(self, value: int) -> None:
        self._core.set_level_total(self._side.value, self.price_ticks, int(value))

    @property
    def orders(self) -> dict:
        rows = self._core.level_orders(self._side.value, self.price_ticks)
        if not rows:
            return {}
        return {row[0]: _take_row(self._book, row) for row in rows}

    def __len__(self) -> int:
        return len(self.orders)

    def __iter__(self):
        return iter(self.orders.values())


class LimitOrderBook:
    """Rust-backed book. Same methods as the Python reference in ``lob.py``."""

    backend = "rust"

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._core = cockpit_quant.BookCore(symbol)
        self._live: dict[str, object] = {}

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
    ):
        return _take_row(self, _call(
            self._core.add, order_id, side.value, int(price_ticks), int(qty), int(seq), int(ts), owner,
        ), fresh=True)

    def cancel(self, order_id: str):
        row = _call(self._core.cancel, order_id)
        order = _cached(self, row[0])
        self._live.pop(row[0], None)
        return order if order is not None else _order(row)

    def reduce(self, order_id: str, qty: int):
        return _take_row(self, _call(self._core.reduce, order_id, int(qty)), zero_leaves=True)

    def replace(
        self,
        order_id: str,
        *,
        new_price_ticks: int | None = None,
        new_qty: int | None = None,
        seq: int = 0,
        ts: int = 0,
    ):
        return _take_row(self, _call(
            self._core.replace,
            order_id,
            None if new_price_ticks is None else int(new_price_ticks),
            None if new_qty is None else int(new_qty),
            int(seq),
            int(ts),
        ), fresh=True)

    def get(self, order_id: str):
        row = self._core.get(order_id)
        return None if row is None else _take_row(self, row)

    def __contains__(self, order_id: object) -> bool:
        if not isinstance(order_id, str):
            return False
        return self._core.contains(order_id)

    def __len__(self) -> int:
        return len(self._core)

    @property
    def _index(self) -> dict:
        return {row[0]: _take_row(self, row) for row in self._core.orders()}

    def resting_at(self, index: int):
        """One resting order in insertion order, without copying the index.

        ``_index`` builds a ``RestingOrder`` for every live id. The synthetic
        feed used that to pick one cancel target, and the copies dominated the
        generator. This asks the book for that one row.
        """
        if type(index) is not int or index < 0:
            raise IndexError(index)
        try:
            row = self._core.order_at(index)
        except IndexError:
            raise IndexError(index) from None
        return _take_row(self, row)

    @property
    def _bid_prices(self) -> _Ladder:
        return _Ladder(self._core, "buy")

    @property
    def _ask_prices(self) -> _Ladder:
        return _Ladder(self._core, "sell")

    def queue_position(self, order_id: str) -> int:
        return _call(self._core.queue_position, order_id)

    def queue_progress(self, order_id: str) -> float:
        return _call(self._core.queue_progress, order_id)

    def best_bid(self):
        return self._core.best("buy")

    def best_ask(self):
        return self._core.best("sell")

    def best(self, side: Side):
        return self._core.best(side.value)

    @property
    def spread_ticks(self):
        return self._core.spread_ticks()

    @property
    def mid_ticks(self):
        return self._core.mid_ticks()

    @property
    def is_crossed(self) -> bool:
        return self._core.is_crossed()

    def level_qty(self, side: Side, price_ticks: int) -> int:
        return self._core.level_qty(side.value, int(price_ticks))

    def level(self, side: Side, price_ticks: int):
        if not self._core.has_level(side.value, int(price_ticks)):
            return None
        return PriceLevel(self, side, int(price_ticks))

    def prices(self, side: Side) -> list[int]:
        return list(self._core.prices(side.value))

    def depth(self, side: Side, levels: int = 5) -> list:
        if levels < 0:
            # Match the reference's ``prices[:levels]`` slice semantics.
            levels = len(range(len(self._core.prices(side.value)))[:levels])
        return [
            _LevelSnapshot(price_ticks=price, qty=qty, order_count=count)
            for price, qty, count in self._core.depth(side.value, levels)
        ]

    def total_qty(self, side: Side) -> int:
        return _call(self._core.total_qty, side.value)

    def our_orders(self, owner: str | None = None) -> list:
        return [
            order for order in self._index.values()
            if order.is_ours and (owner is None or order.owner == owner)
        ]

    def snapshot(self, *, levels: int = 5, seq: int | None = None, ts: int | None = None):
        last_seq, last_ts = self._core.last_stamps()
        return _BookSnapshot(
            symbol=self.symbol,
            seq=last_seq if seq is None else seq,
            ts=last_ts if ts is None else ts,
            bids=tuple(self.depth(Side.BUY, levels)),
            asks=tuple(self.depth(Side.SELL, levels)),
        )

    def check_integrity(self) -> None:
        _call(self._core.check_integrity)

    def apply_many(self, ops):
        """Apply add, cancel, reduce, replace, and match ops in one call.

        Each op is a tuple. Sides, time-in-force, and self-trade values are the
        same strings the enums use (``"buy"``, ``"day"``, ``"cancel_resting"``):

        * ``("a", order_id, side, price_ticks, qty, seq, ts, owner)``
        * ``("c", order_id)``
        * ``("r", order_id, qty)``
        * ``("p", order_id, new_price_ticks, new_qty, seq, ts)`` — price or qty may be None
        * ``("m", order_id, side, qty, price_ticks, tif, owner, stp, seq, exchange_ts)``
          — ``price_ticks`` None is a market order

        Returns one tagged tuple per op. The tag is the op kind. Order ops carry
        the same fields a single call returns; a match op carries the matcher
        payload (status, filled, remaining, trades, fills, resting id, stp ids).
        Fill timestamps stay in Python: this payload is the book mutation only.

        A bad op raises the same exception the single-call method would. Ops
        before it stay applied, including on any ``RestingOrder`` the caller
        already holds.

        A generator costs one native call per op so a book read between yields
        sees the earlier op. Pass a list when speed matters.
        """
        if isinstance(ops, (list, tuple)):
            return self._apply_sequence(list(ops))
        # A generator runs its body on each pull. Apply the op it just yielded
        # before asking for the next one, so a read of the book between yields
        # sees the earlier op. Listing it first would run those reads too early.
        public: list = []
        for op in ops:
            public.extend(self._apply_chunk([op]))
        return public

    def _apply_sequence(self, ops: list):
        if not self._live:
            return self._apply_chunk(ops)
        # A match ends its chunk. The next op is absorbed only after the held
        # qty from that match is applied, so a later cancel cannot look like a fill.
        public: list = []
        start = 0
        for index, op in enumerate(ops):
            if isinstance(op, tuple) and op and op[0] == "m":
                public.extend(self._apply_chunk(ops[start:index + 1]))
                start = index + 1
        if start < len(ops):
            public.extend(self._apply_chunk(ops[start:]))
        return public

    def _apply_chunk(self, chunk: list) -> list:
        try:
            rows = self._core.apply_many(chunk)
        except Exception as exc:
            applied = getattr(exc, "applied", None) or []
            if applied:
                _absorb_batch(self, applied)
            # A failed match may already have traded or cancelled. Those ids are
            # on the error. An id that is merely missing was not filled.
            _apply_effects(self, getattr(exc, "touched", ()), getattr(exc, "stp_cancelled", ()))
            if isinstance(exc, (
                cockpit_quant.NotFound,
                cockpit_quant.IntegrityError,
                cockpit_quant.BookValueError,
            )):
                _raise(exc)
            raise
        return _absorb_batch(self, rows)

    def apply_quiet(self, ops) -> int:
        """Same sequence as ``apply_many``, returning only how many ops applied.

        The quiet fast path exists only when nobody holds a ``RestingOrder``.
        Otherwise the rows still update retained orders, so a held
        object cannot go stale after a quiet reduce, cancel, replace, or fill.
        A generator is applied one op at a time, same as ``apply_many``.
        """
        if not isinstance(ops, (list, tuple)):
            return len(self.apply_many(ops))
        if self._live:
            return len(self.apply_many(ops))
        return _call(self._core.apply_quiet, ops)


def match_core(book: LimitOrderBook, order, *, seq: int, exchange_ts: int, recv_ts: int, ingest_ts: int):
    """Run the Rust matcher and rehydrate Python ``Fill`` / ``MatchResult`` objects."""
    from quant.book.matching import MatchResult, MatchStatus
    from quant.events import Fill

    price = None if order.price_ticks is None else int(order.price_ticks)
    try:
        status, filled, remaining, trades, fills, resting, stp, touched = book._core.match_order(
            order.order_id,
            order.side.value,
            int(order.qty),
            price,
            order.tif.value,
            order.owner,
            order.stp.value,
            int(seq),
            int(exchange_ts),
        )
    except (cockpit_quant.NotFound, cockpit_quant.IntegrityError, cockpit_quant.BookValueError) as exc:
        # Trades and self-trade cancels may already have landed. Apply those
        # ids only; a missing id is not a fill.
        _apply_effects(book, getattr(exc, "touched", ()), getattr(exc, "stp_cancelled", ()))
        _raise(exc)
    _apply_effects(book, touched, stp)
    return MatchResult(
        status=MatchStatus(status),
        filled_qty=filled,
        remaining_qty=remaining,
        fills=[
            Fill(
                symbol=book.symbol,
                seq=seq,
                exchange_ts=exchange_ts,
                recv_ts=recv_ts,
                ingest_ts=ingest_ts,
                order_id=order_id,
                side=_side(side),
                price_ticks=fill_price,
                qty=qty,
                liquidity=Liquidity.MAKER if liquidity == "maker" else Liquidity.TAKER,
                queue_position_at_entry=queue_at_entry,
            )
            for order_id, side, fill_price, qty, liquidity, queue_at_entry in fills
        ],
        trades=[(int(px), int(sz)) for px, sz in trades],
        resting_order_id=resting,
        stp_cancelled=list(stp),
    )

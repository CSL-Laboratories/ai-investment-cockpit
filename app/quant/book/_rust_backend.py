"""Python face of the Rust FIFO book.

The extension owns the queues. This module only adapts them to the dataclasses
and exception types ``quant.book.lob`` already exports, so callers — including
the existing tests — do not change.
"""
from __future__ import annotations

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


class PriceLevel:
    """Live view of one price. ``total_qty`` is writable so the integrity control can fail."""

    def __init__(self, core, side: Side, price: int) -> None:
        self._core = core
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
        return {row[0]: _order(row) for row in rows}

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
        return _order(_call(
            self._core.add, order_id, side.value, int(price_ticks), int(qty), int(seq), int(ts), owner,
        ))

    def cancel(self, order_id: str):
        return _order(_call(self._core.cancel, order_id))

    def reduce(self, order_id: str, qty: int):
        return _order(_call(self._core.reduce, order_id, int(qty)))

    def replace(
        self,
        order_id: str,
        *,
        new_price_ticks: int | None = None,
        new_qty: int | None = None,
        seq: int = 0,
        ts: int = 0,
    ):
        return _order(_call(
            self._core.replace,
            order_id,
            None if new_price_ticks is None else int(new_price_ticks),
            None if new_qty is None else int(new_qty),
            int(seq),
            int(ts),
        ))

    def get(self, order_id: str):
        row = self._core.get(order_id)
        return None if row is None else _order(row)

    def __contains__(self, order_id: object) -> bool:
        if not isinstance(order_id, str):
            return False
        return self._core.contains(order_id)

    def __len__(self) -> int:
        return len(self._core)

    @property
    def _index(self) -> dict:
        return {row[0]: _order(row) for row in self._core.orders()}

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
        return PriceLevel(self._core, side, int(price_ticks))

    def prices(self, side: Side) -> list[int]:
        return list(self._core.prices(side.value))

    def depth(self, side: Side, levels: int = 5) -> list:
        return [
            _LevelSnapshot(price_ticks=price, qty=qty, order_count=count)
            for price, qty, count in self._core.depth(side.value, levels)
        ]

    def total_qty(self, side: Side) -> int:
        return self._core.total_qty(side.value)

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


def match_core(book: LimitOrderBook, order, *, seq: int, exchange_ts: int, recv_ts: int, ingest_ts: int):
    """Run the Rust matcher and rehydrate Python ``Fill`` / ``MatchResult`` objects."""
    from quant.book.matching import MatchResult, MatchStatus
    from quant.events import Fill

    price = None if order.price_ticks is None else int(order.price_ticks)
    try:
        status, filled, remaining, trades, fills, resting, stp = book._core.match_order(
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
        _raise(exc)
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

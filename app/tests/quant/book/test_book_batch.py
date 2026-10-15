"""CSL-42 — one batch call agrees with single calls and with the Python book.

Skipped when the extension is not installed. ``apply_many`` returns the per-op
payload; ``apply_quiet`` applies the same sequence and returns only the count.
"""
from __future__ import annotations

import random

import pytest

from quant.book.lob import LimitOrderBook, OrderNotFound, PythonLimitOrderBook
from quant.book.matching import (
    AggressiveOrder,
    MatchStatus,
    SelfTradePrevention,
    TimeInForce,
    match,
)
from quant.events import Side

pytestmark = pytest.mark.skipif(
    getattr(LimitOrderBook, "backend", "python") != "rust",
    reason="cockpit_quant extension is not installed",
)

PRICES = (98, 99, 100, 101, 102)
SIDES = ("buy", "sell")
TIFS = ("day", "gtc", "ioc", "fok")
STPS = ("cancel_resting", "cancel_aggressing", "allow")
OWNERS = (None, "mm", "pairs")


def _ids(book) -> list[str]:
    return list(book._index)


def _state(book) -> dict:
    orders = {}
    for order_id in _ids(book):
        order = book.get(order_id)
        orders[order_id] = (
            order.side,
            order.price_ticks,
            order.qty,
            order.original_qty,
            order.owner,
            order.qty_ahead_at_entry,
            order.entry_seq,
            order.entry_ts,
            book.queue_position(order_id),
        )
    return {
        "ids": _ids(book),
        "len": len(book),
        "bid": book.best_bid(),
        "ask": book.best_ask(),
        "spread": book.spread_ticks,
        "mid": book.mid_ticks,
        "crossed": book.is_crossed,
        "bid_qty": book.total_qty(Side.BUY),
        "ask_qty": book.total_qty(Side.SELL),
        "orders": orders,
    }


def _order_row(kind: str, order) -> tuple:
    return (
        kind,
        order.order_id,
        order.side.value,
        order.price_ticks,
        order.qty,
        order.entry_seq,
        order.entry_ts,
        order.owner,
        order.original_qty,
        order.qty_ahead_at_entry,
    )


def _match_row(result) -> tuple:
    return (
        "m",
        result.status.value,
        result.filled_qty,
        result.remaining_qty,
        [tuple(trade) for trade in result.trades],
        [
            (
                fill.order_id,
                fill.side.value,
                fill.price_ticks,
                fill.qty,
                fill.liquidity.value,
                fill.queue_position_at_entry,
            )
            for fill in result.fills
        ],
        result.resting_order_id,
        list(result.stp_cancelled),
    )


def _norm(row) -> tuple:
    if row[0] != "m":
        return tuple(row)
    kind, status, filled, remaining, trades, fills, resting, stp = row
    return (
        kind,
        status,
        filled,
        remaining,
        [tuple(trade) for trade in trades],
        [tuple(fill) for fill in fills],
        resting,
        list(stp),
    )


def _apply(book, op):
    kind = op[0]
    if kind == "a":
        _, order_id, side, price, qty, seq, ts, owner = op
        return _order_row(kind, book.add(
            order_id=order_id,
            side=Side(side),
            price_ticks=price,
            qty=qty,
            seq=seq,
            ts=ts,
            owner=owner,
        ))
    if kind == "c":
        return _order_row(kind, book.cancel(op[1]))
    if kind == "r":
        return _order_row(kind, book.reduce(op[1], op[2]))
    if kind == "p":
        _, order_id, price, qty, seq, ts = op
        return _order_row(kind, book.replace(
            order_id,
            new_price_ticks=price,
            new_qty=qty,
            seq=seq,
            ts=ts,
        ))
    _, order_id, side, qty, price, tif, owner, stp, seq, ts = op
    result = match(
        book,
        AggressiveOrder(
            order_id=order_id,
            side=Side(side),
            qty=qty,
            price_ticks=price,
            tif=TimeInForce(tif),
            owner=owner,
            stp=SelfTradePrevention(stp),
        ),
        seq=seq,
        exchange_ts=ts,
    )
    return _match_row(result)


def _legal_script(seed: int, steps: int = 160) -> list[tuple]:
    """Ops a Python book accepts, including matches that reshape the queue."""
    rng = random.Random(seed)
    shadow = PythonLimitOrderBook("SCRIPT")
    ops = []
    counter = 0
    for step in range(steps):
        live = _ids(shadow)
        kind = rng.choice(("a", "c", "r", "p", "m"))
        if kind != "a" and not live:
            kind = "a"
        if kind == "a":
            counter += 1
            qty = rng.randint(1, 400)
            op = (
                "a",
                f"o{counter}",
                rng.choice(SIDES),
                rng.choice(PRICES),
                qty,
                step,
                step * 10,
                rng.choice(OWNERS),
            )
        elif kind == "c":
            op = ("c", rng.choice(live))
        elif kind == "r":
            order_id = rng.choice(live)
            op = ("r", order_id, rng.randint(1, shadow.get(order_id).qty))
        elif kind == "p":
            order_id = rng.choice(live)
            price = None if rng.random() < 0.2 else rng.choice(PRICES)
            qty = None if rng.random() < 0.2 else rng.randint(1, 400)
            if price is None and qty is None and rng.random() < 0.5:
                qty = rng.randint(1, 400)
            op = ("p", order_id, price, qty, step, step)
        else:
            tif = rng.choice(TIFS)
            price = None if rng.random() < 0.15 else rng.choice(PRICES)
            if price is None and tif == "fok":
                tif = "ioc"
            op = (
                "m",
                f"agg{step}",
                rng.choice(SIDES),
                rng.randint(1, 700),
                price,
                tif,
                rng.choice(OWNERS),
                rng.choice(STPS),
                step,
                1_000 + step,
            )
        _apply(shadow, op)
        ops.append(op)
    return ops


def _replay(book_cls, ops):
    book = book_cls("BATCH")
    rows = [_norm(_apply(book, op)) for op in ops]
    return book, rows


@pytest.mark.parametrize("seed", range(6))
def test_batch_matches_python_and_single_calls(seed):
    ops = _legal_script(seed)
    python_book, python_rows = _replay(PythonLimitOrderBook, ops)
    single_book, single_rows = _replay(LimitOrderBook, ops)
    batched = LimitOrderBook("BATCH")
    batch_rows = [_norm(row) for row in batched.apply_many(ops)]
    quiet = LimitOrderBook("BATCH")
    applied = quiet.apply_quiet(ops)

    assert batch_rows == python_rows
    assert batch_rows == single_rows
    assert _state(batched) == _state(python_book)
    assert _state(quiet) == _state(python_book)
    assert _state(single_book) == _state(python_book)
    assert applied == len(ops)
    batched.check_integrity()
    quiet.check_integrity()
    python_book.check_integrity()


def test_replace_with_missing_price_or_qty_keeps_the_other_field():
    ops = [
        ("a", "a", "buy", 100, 10, 1, 1, "mm"),
        ("a", "b", "buy", 100, 7, 2, 2, None),
        ("p", "b", None, None, 9, 9),
        ("p", "a", 101, None, 10, 10),
        ("p", "a", None, 4, 11, 11),
    ]
    python_book, python_rows = _replay(PythonLimitOrderBook, ops)
    batched = LimitOrderBook("BATCH")
    assert [_norm(row) for row in batched.apply_many(ops)] == python_rows
    assert _state(batched) == _state(python_book)
    assert batched.get("a").price_ticks == 101
    assert batched.get("a").qty == 4
    assert batched.get("b").price_ticks == 100
    assert batched.get("b").qty == 7
    assert batched.queue_position("b") == 0


def test_empty_batch_is_a_no_op():
    book = LimitOrderBook("BATCH")
    book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=1, seq=1, ts=1)
    assert book.apply_many([]) == []
    assert book.apply_quiet([]) == 0
    assert len(book) == 1


def test_failed_op_keeps_the_prefix_and_matches_python():
    ops = [
        ("a", "1", "buy", 100, 5, 1, 1, None),
        ("a", "1", "sell", 101, 5, 2, 2, None),
    ]
    python_book = PythonLimitOrderBook("BATCH")
    with pytest.raises(ValueError, match="duplicate"):
        _apply(python_book, ops[0])
        _apply(python_book, ops[1])
    rust_book = LimitOrderBook("BATCH")
    with pytest.raises(ValueError, match="duplicate"):
        rust_book.apply_many(ops)
    assert _state(rust_book) == _state(python_book)

    rust_book.apply_many([("a", "2", "sell", 101, 4, 3, 3, None)])
    with pytest.raises(OrderNotFound):
        rust_book.apply_quiet([("c", "missing")])
    assert "2" in rust_book
    assert rust_book.get("2").qty == 4

    with pytest.raises(ValueError, match="reduce qty"):
        rust_book.apply_many([("r", "2", 0)])
    assert rust_book.get("2").qty == 4


def test_malformed_ops_raise_before_touching_the_book():
    book = LimitOrderBook("BATCH")
    with pytest.raises(ValueError, match="must be a tuple"):
        book.apply_many(["a"])
    with pytest.raises(ValueError, match="unknown batch op"):
        book.apply_many([("z",)])
    with pytest.raises(ValueError, match="expects 2 fields"):
        book.apply_quiet([("c", "nope", "extra")])
    with pytest.raises(ValueError, match="unknown tif"):
        book.apply_many([("m", "x", "buy", 1, 100, "week", None, "allow", 1, 1)])
    assert len(book) == 0


def test_batch_match_status_strings_are_the_python_enum():
    ops = [
        ("a", "ask", "sell", 100, 5, 1, 1, None),
        ("m", "hit", "buy", 5, 100, "ioc", "mm", "allow", 2, 2),
    ]
    batched = LimitOrderBook("BATCH")
    rows = batched.apply_many(ops)
    assert rows[1][0] == "m"
    assert rows[1][1] == MatchStatus.FILLED.value
    assert rows[1][2] == 5
    python_book, python_rows = _replay(PythonLimitOrderBook, ops)
    assert [_norm(row) for row in rows] == python_rows
    assert _state(batched) == _state(python_book)

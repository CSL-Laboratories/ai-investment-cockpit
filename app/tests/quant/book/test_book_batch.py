"""CSL-42 — one batch call agrees with single calls and with the Python book.

``apply_many`` returns the per-op payload; ``apply_quiet`` applies the same
sequence and returns only the count. The required hosted Linux job installs
the extension and runs this file (``ci.yml`` linux). A checkout without the
wheel still skips, because the batch API is the Rust book.
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


def test_batch_ops_keep_a_retained_order_as_live_as_the_python_book():
    """A caller that kept the object from ``add`` sees the same qty the Python book edits."""
    rust = LimitOrderBook("BATCH")
    python = PythonLimitOrderBook("BATCH")
    held = rust.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=1, ts=1)
    py = python.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=1, ts=1)

    rust.apply_many([("r", "a", 3)])
    python.reduce("a", 3)
    assert held is rust.get("a")
    assert held.qty == py.qty == 7

    rust.apply_quiet([("r", "a", held.qty)])
    python.reduce("a", py.qty)
    assert held.qty == py.qty == 0
    assert rust.get("a") is None and python.get("a") is None

    resting = rust.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, seq=2, ts=2)
    py_rest = python.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, seq=2, ts=2)
    rust.apply_many([("m", "hit", "buy", 4, 100, "day", None, "allow", 3, 3)])
    match(python, AggressiveOrder(order_id="hit", side=Side.BUY, qty=4, price_ticks=100, stp=SelfTradePrevention.ALLOW))
    assert resting is rust.get("ask")
    assert resting.qty == py_rest.qty == 6
    rust.apply_quiet([("c", "ask")])
    python.cancel("ask")
    assert resting.qty == py_rest.qty == 6
    assert rust.get("ask") is None and python.get("ask") is None

    own = rust.add(order_id="own", side=Side.SELL, price_ticks=102, qty=5, owner="mm", seq=4, ts=4)
    py_own = python.add(order_id="own", side=Side.SELL, price_ticks=102, qty=5, owner="mm", seq=4, ts=4)
    rust.apply_quiet([("m", "self", "buy", 5, 102, "day", "mm", "cancel_resting", 5, 5)])
    match(
        python,
        AggressiveOrder(
            order_id="self", side=Side.BUY, qty=5, price_ticks=102, owner="mm",
            stp=SelfTradePrevention.CANCEL_RESTING,
        ),
    )
    assert own.qty == py_own.qty == 5
    assert rust.get("own") is None and python.get("own") is None

    old = rust.add(order_id="c", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=6, ts=6)
    py_old = python.add(order_id="c", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=6, ts=6)
    rust.apply_many([("p", "c", 101, 4, 8, 8)])
    py_new = python.replace("c", new_price_ticks=101, new_qty=4, seq=8, ts=8)
    assert old is not rust.get("c")
    assert old.qty == py_old.qty == 10
    assert rust.get("c").qty == py_new.qty == 4

    cancelled = rust.add(order_id="b", side=Side.BUY, price_ticks=99, qty=8, owner="mm", seq=9, ts=9)
    py_cancel = python.add(order_id="b", side=Side.BUY, price_ticks=99, qty=8, owner="mm", seq=9, ts=9)
    rust.apply_quiet([("c", "b")])
    python.cancel("b")
    assert cancelled.qty == py_cancel.qty == 8
    assert rust.get("b") is None and python.get("b") is None

    prefix = rust.add(order_id="d", side=Side.BUY, price_ticks=100, qty=10, seq=10, ts=10)
    py_prefix = python.add(order_id="d", side=Side.BUY, price_ticks=100, qty=10, seq=10, ts=10)
    with pytest.raises(ValueError, match="duplicate"):
        rust.apply_many([
            ("r", "d", 4),
            ("a", "d", "sell", 101, 1, 11, 11, None),
        ])
    with pytest.raises(ValueError, match="duplicate"):
        python.reduce("d", 4)
        python.add(order_id="d", side=Side.SELL, price_ticks=101, qty=1, seq=11, ts=11)
    assert prefix is rust.get("d")
    assert prefix.qty == py_prefix.qty == 6
    rust.check_integrity()
    python.check_integrity()


def test_batch_accepts_a_generator_that_reads_the_book():
    book = LimitOrderBook("GEN")

    def ops():
        yield ("a", "a", "buy", 100, 5, 1, 1, None)
        bid = book.best_bid()
        yield ("a", "b", "buy", bid - 1, 5, 2, 2, None)

    assert len(book.apply_many(ops())) == 2
    assert len(book) == 2


def test_match_then_cancel_keeps_post_match_qty_on_retained_order():
    rust = LimitOrderBook("MC")
    python = PythonLimitOrderBook("MC")
    held = rust.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, seq=1, ts=1)
    py = python.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, seq=1, ts=1)
    rust.apply_many([
        ("m", "hit", "buy", 4, 100, "day", None, "allow", 2, 2),
        ("c", "ask"),
    ])
    match(python, AggressiveOrder(order_id="hit", side=Side.BUY, qty=4, price_ticks=100, stp=SelfTradePrevention.ALLOW))
    python.cancel("ask")
    assert held.qty == py.qty == 6


def test_failed_match_resyncs_retained_orders():
    rust = LimitOrderBook("FM")
    ask = rust.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=4, seq=1, ts=1)
    rust.add(order_id="hit", side=Side.BUY, price_ticks=90, qty=1, seq=2, ts=2)
    with pytest.raises(Exception):
        rust.apply_many([("m", "hit", "buy", 5, 100, "day", None, "allow", 3, 3)])
    assert rust.get("ask") is None
    assert ask.qty == 0


def test_batch_stp_cancel_plus_failed_remainder_keeps_held_qty():
    """A self-trade cancel before a duplicate remainder is not a fill."""
    rust = LimitOrderBook("STP")
    python = PythonLimitOrderBook("STP")
    mine = rust.add(order_id="mine", side=Side.SELL, price_ticks=100, qty=4, owner="mm", seq=1, ts=1)
    py = python.add(order_id="mine", side=Side.SELL, price_ticks=100, qty=4, owner="mm", seq=1, ts=1)
    rust.add(order_id="dup", side=Side.BUY, price_ticks=90, qty=1, owner="other", seq=2, ts=2)
    python.add(order_id="dup", side=Side.BUY, price_ticks=90, qty=1, owner="other", seq=2, ts=2)
    op = ("m", "dup", "buy", 6, 100, "day", "mm", "cancel_resting", 3, 3)
    with pytest.raises(ValueError, match="duplicate"):
        rust.apply_many([op])
    with pytest.raises(ValueError, match="duplicate"):
        match(
            python,
            AggressiveOrder(
                order_id="dup", side=Side.BUY, qty=6, price_ticks=100,
                tif=TimeInForce.DAY, owner="mm",
                stp=SelfTradePrevention.CANCEL_RESTING,
            ),
        )
    assert mine.qty == py.qty == 4
    assert rust.get("mine") is None and python.get("mine") is None

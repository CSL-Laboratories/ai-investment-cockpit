"""CSL-42 — the Rust book and matcher agree with the Python reference.

Skipped when the extension is not installed, so the Python-only gate still runs
the reference implementation. With the extension installed, ``LimitOrderBook``
is the Rust class and these cases are the differential check.
"""
from __future__ import annotations

import importlib.util
import random

import pytest

from quant.book.lob import LimitOrderBook, PythonLimitOrderBook
from quant.book.matching import (
    AggressiveOrder,
    SelfTradePrevention,
    TimeInForce,
    match,
)
from quant.events import Side

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("cockpit_quant") is None,
    reason="cockpit_quant extension is not installed",
)

PRICES = (98, 99, 100, 101, 102)


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
            order.filled_qty,
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
        "levels": {
            (side, price): book.level_qty(side, price)
            for side in (Side.BUY, Side.SELL)
            for price in PRICES
        },
        "orders": orders,
    }


def _fill_key(fill):
    return (
        fill.order_id,
        fill.side,
        fill.price_ticks,
        fill.qty,
        fill.liquidity,
        fill.queue_position_at_entry,
        fill.seq,
        fill.exchange_ts,
        fill.recv_ts,
        fill.ingest_ts,
        fill.signed_qty,
    )


def _result_key(result):
    return {
        "status": result.status,
        "filled": result.filled_qty,
        "remaining": result.remaining_qty,
        "trades": list(result.trades),
        "resting": result.resting_order_id,
        "stp": list(result.stp_cancelled),
        "fills": [_fill_key(fill) for fill in result.fills],
        "notional": result.notional_ticks,
        "avg": result.avg_price_ticks,
    }


def test_installing_the_extension_rebinds_the_public_book():
    import cockpit_quant

    assert LimitOrderBook.backend == "rust"
    assert PythonLimitOrderBook.backend == "python"
    assert LimitOrderBook is not PythonLimitOrderBook
    assert cockpit_quant.BookCore("S").symbol() == "S"


def test_scripted_queue_and_match_agree():
    rust, py = LimitOrderBook("TEST"), PythonLimitOrderBook("TEST")
    for book in (rust, py):
        book.add(order_id="a1", side=Side.SELL, price_ticks=101, qty=100, seq=1, ts=10)
        book.add(order_id="ours", side=Side.SELL, price_ticks=101, qty=50, seq=2, ts=11, owner="mm")
        book.add(order_id="bid", side=Side.BUY, price_ticks=99, qty=80, seq=3, ts=12, owner="pairs")
    assert _state(rust) == _state(py)

    order = AggressiveOrder(
        order_id="agg", side=Side.BUY, qty=120, price_ticks=101, owner="momentum",
    )
    rust_result = match(rust, order, seq=9, exchange_ts=100, recv_ts=130, ingest_ts=140)
    py_result = match(py, order, seq=9, exchange_ts=100, recv_ts=130, ingest_ts=140)
    assert _result_key(rust_result) == _result_key(py_result)
    assert _state(rust) == _state(py)
    rust.check_integrity()
    py.check_integrity()


@pytest.mark.parametrize("seed", range(8))
def test_random_operations_agree_with_the_python_book(seed):
    rng = random.Random(seed)
    rust, py = LimitOrderBook("SWEEP"), PythonLimitOrderBook("SWEEP")
    counter = 0
    for step in range(250):
        live = _ids(py)
        op = rng.choice(["add", "cancel", "reduce", "replace", "match"])
        if op != "add" and not live:
            op = "add"
        if op == "add":
            counter += 1
            kwargs = dict(
                order_id=f"o{counter}",
                side=rng.choice([Side.BUY, Side.SELL]),
                price_ticks=rng.choice(PRICES),
                qty=rng.randint(1, 400),
                seq=step,
                ts=step * 10,
                owner=rng.choice([None, "mm", "pairs"]),
            )
            rust.add(**kwargs)
            py.add(**kwargs)
            outcome = "add"
        elif op == "cancel":
            order_id = rng.choice(live)
            rust.cancel(order_id)
            py.cancel(order_id)
            outcome = "cancel"
        elif op == "reduce":
            order_id = rng.choice(live)
            qty = rng.randint(1, py.get(order_id).qty)
            rust.reduce(order_id, qty)
            py.reduce(order_id, qty)
            outcome = "reduce"
        elif op == "replace":
            order_id = rng.choice(live)
            new_price, new_qty = rng.choice(PRICES), rng.randint(1, 400)
            rust.replace(order_id, new_price_ticks=new_price, new_qty=new_qty, seq=step, ts=step)
            py.replace(order_id, new_price_ticks=new_price, new_qty=new_qty, seq=step, ts=step)
            outcome = "replace"
        else:
            tif = rng.choice([TimeInForce.DAY, TimeInForce.IOC, TimeInForce.FOK, TimeInForce.GTC])
            price = None if rng.random() < 0.15 else rng.choice(PRICES)
            if price is None and tif is TimeInForce.FOK:
                tif = TimeInForce.IOC
            order = AggressiveOrder(
                order_id=f"agg{step}",
                side=rng.choice([Side.BUY, Side.SELL]),
                qty=rng.randint(1, 700),
                price_ticks=price,
                tif=tif,
                owner=rng.choice([None, "mm", "pairs"]),
                stp=rng.choice(list(SelfTradePrevention)),
            )
            rust_result = match(rust, order, seq=step, exchange_ts=1_000 + step, recv_ts=1_050 + step)
            py_result = match(py, order, seq=step, exchange_ts=1_000 + step, recv_ts=1_050 + step)
            assert _result_key(rust_result) == _result_key(py_result), f"seed={seed} step={step}"
            outcome = "match"
        assert _state(rust) == _state(py), f"seed={seed} step={step} op={outcome}"
        rust.check_integrity()
        py.check_integrity()


def _live_qty_script(book):
    """The reference book's object identity, returned as qtys a caller still holds."""
    held = book.add(order_id="a", side=Side.BUY, price_ticks=100, qty=10, owner="mm", seq=1, ts=1)
    assert book.get("a") is held
    assert book.level(Side.BUY, 100).orders["a"] is held
    assert book.our_orders()[0] is held
    book.reduce("a", 4)
    partial = held.qty
    book.reduce("a", held.qty)
    zero = held.qty
    assert book.get("a") is None

    held = book.add(order_id="b", side=Side.BUY, price_ticks=100, qty=8, owner="mm")
    assert book.cancel("b") is held
    cancelled = held.qty

    held = book.add(order_id="c", side=Side.SELL, price_ticks=101, qty=9, owner="mm", seq=2, ts=2)
    replaced = book.replace("c", new_qty=3, seq=3, ts=3)
    assert replaced is not held
    assert book.get("c") is replaced

    resting = book.add(order_id="ask", side=Side.SELL, price_ticks=100, qty=10, owner="mm")
    match(book, AggressiveOrder(order_id="hit", side=Side.BUY, qty=6, price_ticks=100))
    filled = resting.qty
    assert book.get("ask") is resting
    own = book.add(order_id="own", side=Side.SELL, price_ticks=102, qty=5, owner="mm")
    match(
        book,
        AggressiveOrder(
            order_id="self", side=Side.BUY, qty=5, price_ticks=102, owner="mm",
            stp=SelfTradePrevention.CANCEL_RESTING,
        ),
    )
    return {
        "partial": partial,
        "zero": zero,
        "cancelled": cancelled,
        "old_replace_qty": held.qty,
        "new_replace_qty": replaced.qty,
        "filled": filled,
        "stp_qty": own.qty,
        "stp_gone": book.get("own") is None,
    }


def test_rust_retained_orders_match_the_python_book():
    assert _live_qty_script(LimitOrderBook("LIVE")) == _live_qty_script(PythonLimitOrderBook("LIVE"))


def _failed_remainder_script(book):
    ask = book.add(order_id="a", side=Side.SELL, price_ticks=100, qty=4, owner="mm")
    book.add(order_id="dup", side=Side.BUY, price_ticks=90, qty=1, owner="mm")
    with pytest.raises(Exception):
        match(
            book,
            AggressiveOrder(
                order_id="dup", side=Side.BUY, qty=6, price_ticks=100,
                tif=TimeInForce.DAY, owner="other",
            ),
        )
    return ask.qty, book.get("a") is None


def test_rust_failed_remainder_reconciles_retained_orders():
    assert _failed_remainder_script(LimitOrderBook("DUP")) == _failed_remainder_script(
        PythonLimitOrderBook("DUP")
    )


def _stp_failed_remainder_script(book):
    """Self-trade cancel, then the remainder cannot rest because the id is taken.

    The cancelled order leaves the book. Its held qty stays. Missing is not a fill.
    """
    mine = book.add(order_id="mine", side=Side.SELL, price_ticks=100, qty=4, owner="mm")
    book.add(order_id="dup", side=Side.BUY, price_ticks=90, qty=1, owner="other")
    with pytest.raises(ValueError, match="duplicate"):
        match(
            book,
            AggressiveOrder(
                order_id="dup", side=Side.BUY, qty=6, price_ticks=100,
                tif=TimeInForce.DAY, owner="mm",
                stp=SelfTradePrevention.CANCEL_RESTING,
            ),
        )
    return mine.qty, book.get("mine") is None


def test_rust_stp_cancel_plus_failed_remainder_keeps_held_qty():
    assert _stp_failed_remainder_script(LimitOrderBook("STP")) == _stp_failed_remainder_script(
        PythonLimitOrderBook("STP")
    )


def _id_reuse_script(book):
    """Same id, two endings: a self-trade cancel, and a full fill.

    Cancel leaves the old object's qty. A full fill zeros it. Either way the
    book then holds a new order under that id.
    """
    cancelled = book.add(order_id="X", side=Side.SELL, price_ticks=100, qty=10, owner="mm")
    match(
        book,
        AggressiveOrder(
            order_id="X", side=Side.BUY, qty=15, price_ticks=100, owner="mm",
            stp=SelfTradePrevention.CANCEL_RESTING,
        ),
    )
    rested_after_cancel = book.get("X")
    cancel_case = (
        cancelled.qty,
        rested_after_cancel is not cancelled,
        rested_after_cancel.side,
        rested_after_cancel.qty,
    )
    book.cancel("X")

    filled = book.add(order_id="X", side=Side.SELL, price_ticks=100, qty=10, owner="mm")
    match(
        book,
        AggressiveOrder(
            order_id="X", side=Side.BUY, qty=15, price_ticks=100, owner="other",
            stp=SelfTradePrevention.ALLOW,
        ),
    )
    rested_after_fill = book.get("X")
    fill_case = (
        filled.qty,
        rested_after_fill is not filled,
        rested_after_fill.side,
        rested_after_fill.qty,
    )
    return cancel_case, fill_case


def test_rust_id_reuse_distinguishes_fill_from_self_trade_cancel():
    assert _id_reuse_script(LimitOrderBook("REUSE")) == _id_reuse_script(PythonLimitOrderBook("REUSE"))


def test_rust_qty_domain_is_signed_64_bit():
    """The Rust book is signed 64-bit. Python ints are unbounded.

    ``2**63`` does not fit. Two orders of ``2**62`` each fit alone and their
    level total does not. Both are ``ValueError``, not ``OverflowError``.
    """
    python = PythonLimitOrderBook("DOM")
    python.add(order_id="wide", side=Side.BUY, price_ticks=100, qty=2**63)
    assert python.get("wide").qty == 2**63

    rust = LimitOrderBook("DOM")
    with pytest.raises(ValueError, match="signed 64-bit"):
        rust.add(order_id="wide", side=Side.BUY, price_ticks=100, qty=2**63)
    rust.add(order_id="a", side=Side.BUY, price_ticks=100, qty=2**62)
    with pytest.raises(ValueError, match="signed 64-bit"):
        rust.add(order_id="b", side=Side.BUY, price_ticks=100, qty=2**62)
    assert rust.get("a").qty == 2**62
    rust.check_integrity()

    # Each level fits. Their sum does not, so the book total is refused too.
    python.add(order_id="lo", side=Side.SELL, price_ticks=101, qty=2**62)
    python.add(order_id="hi", side=Side.SELL, price_ticks=102, qty=2**62)
    assert python.total_qty(Side.SELL) == 2**63
    rust.add(order_id="lo", side=Side.SELL, price_ticks=101, qty=2**62)
    rust.add(order_id="hi", side=Side.SELL, price_ticks=102, qty=2**62)
    with pytest.raises(ValueError, match="signed 64-bit"):
        rust.total_qty(Side.SELL)


def test_resting_at_matches_the_python_index_order():
    python = PythonLimitOrderBook("S")
    rust = LimitOrderBook("S")
    rng = random.Random(4)
    for i in range(30):
        side = Side.BUY if rng.random() < 0.5 else Side.SELL
        price = 100 + rng.randrange(-3, 4)
        if side is Side.BUY:
            price = min(price, 99)
        else:
            price = max(price, 101)
        order_id = f"o{i}"
        qty = 100 * rng.randint(1, 4)
        python.add(order_id=order_id, side=side, price_ticks=price, qty=qty, seq=i, ts=i)
        rust.add(order_id=order_id, side=side, price_ticks=price, qty=qty, seq=i, ts=i)
        if i % 4 == 3:
            victim = f"o{rng.randrange(i + 1)}"
            if victim in python:
                python.cancel(victim)
                rust.cancel(victim)
    python_ids = list(python._index)
    assert [rust.resting_at(i).order_id for i in range(len(rust))] == python_ids
    for i, order_id in enumerate(python_ids):
        left = python.resting_at(i)
        right = rust.resting_at(i)
        assert right.order_id == left.order_id == order_id
        assert right.side is left.side
        assert right.price_ticks == left.price_ticks
        assert right.qty == left.qty
    for bad in (len(rust), -1, True, 2**63, 2**64):
        with pytest.raises(IndexError):
            rust.resting_at(bad)
        with pytest.raises(IndexError):
            python.resting_at(bad)


def _resting_ids(book) -> list[str]:
    return [book.resting_at(i).order_id for i in range(len(book))]


def test_resting_at_keeps_insertion_order_through_reduce_and_replace():
    """A partial reduce stays in its slot. A full reduce drops it.

    ``replace`` cancels and re-adds, so the id moves to the end of the index
    on both books. That is the order ``resting_at`` must keep.
    """
    python = PythonLimitOrderBook("S")
    rust = LimitOrderBook("S")

    def add(order_id: str, side: Side, price: int, qty: int, seq: int) -> None:
        python.add(
            order_id=order_id, side=side, price_ticks=price, qty=qty, seq=seq, ts=seq,
        )
        rust.add(
            order_id=order_id, side=side, price_ticks=price, qty=qty, seq=seq, ts=seq,
        )

    def same(expected: list[str]) -> None:
        assert _resting_ids(python) == expected
        assert _resting_ids(rust) == expected

    add("a", Side.BUY, 100, 10, 1)
    add("b", Side.BUY, 100, 20, 2)
    add("c", Side.SELL, 101, 30, 3)
    same(["a", "b", "c"])

    python.reduce("b", 5)
    rust.reduce("b", 5)
    assert python.resting_at(1).qty == rust.resting_at(1).qty == 15
    same(["a", "b", "c"])

    python.reduce("a", 10)
    rust.reduce("a", 10)
    same(["b", "c"])

    python.replace("b", new_qty=12, seq=4, ts=4)
    rust.replace("b", new_qty=12, seq=4, ts=4)
    same(["c", "b"])
    assert python.resting_at(1).qty == rust.resting_at(1).qty == 12
    assert python.resting_at(1).price_ticks == rust.resting_at(1).price_ticks == 100

    python.replace("c", new_price_ticks=102, new_qty=8, seq=5, ts=5)
    rust.replace("c", new_price_ticks=102, new_qty=8, seq=5, ts=5)
    same(["b", "c"])
    assert python.resting_at(1).price_ticks == rust.resting_at(1).price_ticks == 102
    assert python.resting_at(1).qty == rust.resting_at(1).qty == 8

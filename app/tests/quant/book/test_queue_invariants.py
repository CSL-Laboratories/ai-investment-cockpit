"""QT-01 — randomized differential sweep: the real book vs an independent naive model.

WHY A SECOND IMPLEMENTATION AND NOT JUST ASSERTIONS
----------------------------------------------------
``LimitOrderBook`` is fast because it is clever: dict-ordered levels for O(1) cancel,
bisect-maintained price ladders, and an incrementally-maintained ``total_qty`` per level.
Every one of those is an opportunity for slow, silent divergence — a cached total that
drifts by one share, a ladder entry that outlives its level, an order that leaves a level
but stays in the index. None of them crash. All of them produce a book that still looks
well-formed while returning wrong queue positions, and a wrong queue position produces a
backtest that is confidently wrong rather than obviously broken.

So this file contains ``NaiveBook``: the same semantics written the stupid way — a flat
list per level, totals summed on every read, prices sorted on every read. It is far too
slow to use and far too simple to be subtly wrong. The sweep drives both with the same
random operation stream and asserts they agree on every observable, after every operation.

That is a much stronger statement than any assertion the real book could make about itself,
because the two implementations share no code and no data structures. A bug would have to
appear in both, identically, to hide.

DETERMINISM
-----------
Seeded ``random.Random`` per test, following the existing convention in
``tests/risk/test_engine.py`` (this repo has no hypothesis dependency and adding one was
out of scope). A failure is therefore reproducible from the seed printed in the assertion
message, and the sweep is not a flake source.
"""
from __future__ import annotations

import random

import pytest

from quant.book.lob import LimitOrderBook, OrderNotFound
from quant.book.matching import AggressiveOrder, TimeInForce, match
from quant.events import Side

PRICES = (98, 99, 100, 101, 102)
ITERATIONS = 400
SWEEPS = 12


class NaiveBook:
    """The obviously-correct reference. Lists, linear scans, nothing cached.

    Deliberately does NOT mirror the real book's internals — no dicts keyed by order id, no
    sorted ladder, no running totals. If it did, it would reproduce the same bugs and the
    comparison would prove nothing.
    """

    def __init__(self) -> None:
        # side -> list of (price, order_id, qty) in strict arrival order
        self.resting: dict[Side, list[list]] = {Side.BUY: [], Side.SELL: []}

    def add(self, order_id: str, side: Side, price: int, qty: int) -> None:
        self.resting[side].append([price, order_id, qty])

    def _find(self, order_id: str):
        for side in (Side.BUY, Side.SELL):
            for row in self.resting[side]:
                if row[1] == order_id:
                    return side, row
        return None, None

    def cancel(self, order_id: str) -> None:
        side, row = self._find(order_id)
        if row is None:
            raise OrderNotFound(order_id)
        self.resting[side].remove(row)

    def reduce(self, order_id: str, qty: int) -> None:
        side, row = self._find(order_id)
        if row is None:
            raise OrderNotFound(order_id)
        row[2] -= qty
        if row[2] <= 0:
            self.resting[side].remove(row)

    def replace(self, order_id: str, price: int, qty: int) -> None:
        side, row = self._find(order_id)
        if row is None:
            raise OrderNotFound(order_id)
        self.resting[side].remove(row)
        self.resting[side].append([price, order_id, qty])   # back of the queue, always

    def level_qty(self, side: Side, price: int) -> int:
        return sum(r[2] for r in self.resting[side] if r[0] == price)

    def queue_position(self, order_id: str) -> int:
        side, row = self._find(order_id)
        if row is None:
            raise OrderNotFound(order_id)
        ahead = 0
        for candidate in self.resting[side]:
            if candidate is row:
                return ahead
            if candidate[0] == row[0]:
                ahead += candidate[2]
        raise AssertionError("unreachable")

    def best(self, side: Side) -> int | None:
        prices = [r[0] for r in self.resting[side]]
        if not prices:
            return None
        return max(prices) if side is Side.BUY else min(prices)

    def ids(self) -> set[str]:
        return {r[1] for side in (Side.BUY, Side.SELL) for r in self.resting[side]}

    def total(self, side: Side) -> int:
        return sum(r[2] for r in self.resting[side])

    def cross(self, side: Side, limit: int, qty: int) -> list[tuple[int, int]]:
        """Walk the opposite side FIFO, best price first. Returns the trade tape."""
        opposite = Side.SELL if side is Side.BUY else Side.BUY
        levels = sorted({r[0] for r in self.resting[opposite]}, reverse=(opposite is Side.BUY))
        trades: list[tuple[int, int]] = []
        remaining = qty
        for price in levels:
            if remaining <= 0:
                break
            crosses = price <= limit if side is Side.BUY else price >= limit
            if not crosses:
                break
            for row in [r for r in self.resting[opposite] if r[0] == price]:
                if remaining <= 0:
                    break
                traded = min(remaining, row[2])
                row[2] -= traded
                remaining -= traded
                trades.append((price, traded))
                if row[2] == 0:
                    self.resting[opposite].remove(row)
        return trades


def _agree(real: LimitOrderBook, naive: NaiveBook, *, seed: int, step: int, op: str) -> None:
    """Assert every observable matches. The failure message names the seed and the step."""
    where = f"seed={seed} step={step} op={op}"
    real.check_integrity()
    assert real._index.keys() == naive.ids(), f"resting order sets diverged: {where}"
    for side in (Side.BUY, Side.SELL):
        assert real.best(side) == naive.best(side), f"best {side.value} diverged: {where}"
        assert real.total_qty(side) == naive.total(side), f"total {side.value} diverged: {where}"
        for price in PRICES:
            assert real.level_qty(side, price) == naive.level_qty(side, price), (
                f"level {side.value}/{price} diverged: {where}"
            )
    for order_id in naive.ids():
        assert real.queue_position(order_id) == naive.queue_position(order_id), (
            f"queue position of {order_id!r} diverged: {where}"
        )


@pytest.mark.parametrize("seed", range(SWEEPS))
def test_the_real_book_agrees_with_the_naive_model_under_a_random_operation_stream(seed):
    """The core sweep. Adds, cancels, partial reductions, replaces and crossing orders,
    interleaved at random, compared on every observable after every single operation."""
    rng = random.Random(seed)
    real, naive = LimitOrderBook("SWEEP"), NaiveBook()
    counter = 0

    for step in range(ITERATIONS):
        live = sorted(naive.ids())
        op = rng.choices(
            ["add", "cancel", "reduce", "replace", "cross"],
            weights=[45, 20, 15, 10, 10],
        )[0]
        if op != "add" and not live:
            op = "add"

        if op == "add":
            counter += 1
            order_id = f"o{counter}"
            side = rng.choice([Side.BUY, Side.SELL])
            price, qty = rng.choice(PRICES), rng.randint(1, 500)
            real.add(order_id=order_id, side=side, price_ticks=price, qty=qty,
                     owner="mm" if rng.random() < 0.25 else None)
            naive.add(order_id, side, price, qty)
        elif op == "cancel":
            order_id = rng.choice(live)
            real.cancel(order_id)
            naive.cancel(order_id)
        elif op == "reduce":
            order_id = rng.choice(live)
            qty = rng.randint(1, real.get(order_id).qty)
            real.reduce(order_id, qty)
            naive.reduce(order_id, qty)
        elif op == "replace":
            order_id = rng.choice(live)
            price, qty = rng.choice(PRICES), rng.randint(1, 500)
            real.replace(order_id, new_price_ticks=price, new_qty=qty)
            naive.replace(order_id, price, qty)
        else:
            side = rng.choice([Side.BUY, Side.SELL])
            limit, qty = rng.choice(PRICES), rng.randint(1, 800)
            # IOC on both sides: the remainder must not rest, or the two models would need
            # to agree on resting-id generation, which is not what this sweep is testing.
            result = match(real, AggressiveOrder(order_id=f"agg{step}", side=side, qty=qty,
                                                 price_ticks=limit, tif=TimeInForce.IOC))
            expected = naive.cross(side, limit, qty)
            assert result.trades == expected, (
                f"trade tape diverged: seed={seed} step={step} "
                f"{side.value} {qty}@{limit}: real={result.trades} naive={expected}"
            )

        _agree(real, naive, seed=seed, step=step, op=op)


@pytest.mark.parametrize("seed", range(SWEEPS))
def test_quantity_is_conserved_across_every_operation(seed):
    """Independent of the differential check: track adds and removals by hand and assert
    the book's own totals never drift. Catches an incremental aggregate that leaks."""
    rng = random.Random(1_000 + seed)
    book = LimitOrderBook("CONSERVE")
    expected = 0
    counter = 0

    for _ in range(ITERATIONS):
        live = [o.order_id for o in book._index.values()]
        op = rng.choices(["add", "cancel", "reduce", "cross"], weights=[50, 20, 20, 10])[0]
        if op != "add" and not live:
            op = "add"

        if op == "add":
            counter += 1
            qty = rng.randint(1, 500)
            book.add(order_id=f"o{counter}", side=rng.choice([Side.BUY, Side.SELL]),
                     price_ticks=rng.choice(PRICES), qty=qty)
            expected += qty
        elif op == "cancel":
            order_id = rng.choice(live)
            expected -= book.get(order_id).qty
            book.cancel(order_id)
        elif op == "reduce":
            order_id = rng.choice(live)
            qty = rng.randint(1, book.get(order_id).qty)
            book.reduce(order_id, qty)
            expected -= qty
        else:
            side = rng.choice([Side.BUY, Side.SELL])
            result = match(book, AggressiveOrder(order_id=f"x{counter}", side=side,
                                                 qty=rng.randint(1, 800),
                                                 price_ticks=rng.choice(PRICES),
                                                 tif=TimeInForce.IOC))
            expected -= result.filled_qty

        actual = book.total_qty(Side.BUY) + book.total_qty(Side.SELL)
        assert actual == expected, f"quantity leaked: seed={seed} expected={expected} actual={actual}"
        book.check_integrity()


@pytest.mark.parametrize("seed", range(SWEEPS))
def test_matching_never_trades_through_the_limit_price(seed):
    """No trade may print worse than the aggressor's limit. A matcher that walks one level
    too far produces fills at prices the strategy never agreed to — and since the resting
    side sets the price, the error shows up as an unexplained cost, not an exception."""
    rng = random.Random(2_000 + seed)
    book = LimitOrderBook("LIMIT")
    for i in range(60):
        book.add(order_id=f"o{i}", side=rng.choice([Side.BUY, Side.SELL]),
                 price_ticks=rng.choice(PRICES), qty=rng.randint(1, 300))

    for i in range(120):
        side = rng.choice([Side.BUY, Side.SELL])
        limit = rng.choice(PRICES)
        result = match(book, AggressiveOrder(order_id=f"agg{i}", side=side,
                                             qty=rng.randint(1, 400), price_ticks=limit,
                                             tif=TimeInForce.IOC))
        for price, _qty in result.trades:
            if side is Side.BUY:
                assert price <= limit, f"bought through the limit: {price} > {limit} (seed={seed})"
            else:
                assert price >= limit, f"sold through the limit: {price} < {limit} (seed={seed})"
        book.check_integrity()
        if len(book) < 10:
            for j in range(20):
                book.add(order_id=f"r{i}_{j}", side=rng.choice([Side.BUY, Side.SELL]),
                         price_ticks=rng.choice(PRICES), qty=rng.randint(1, 300))


def test_the_sweep_would_catch_a_broken_book():
    """CONTROL. The differential comparison is only meaningful if it can fail.

    Feed the two models divergent operations on purpose and confirm ``_agree`` raises. Without
    this, a comparison that silently compared nothing would let all the sweeps above pass.
    """
    real, naive = LimitOrderBook("CONTROL"), NaiveBook()
    real.add(order_id="a", side=Side.BUY, price_ticks=100, qty=100)
    naive.add("a", Side.BUY, 100, 100)
    _agree(real, naive, seed=-1, step=0, op="control")

    real.reduce("a", 40)          # only the real book is told
    with pytest.raises(AssertionError):
        _agree(real, naive, seed=-1, step=1, op="control")


def test_fifo_order_is_preserved_under_a_random_stream_of_same_price_adds():
    """Price-time priority, stated directly: at one level, orders must fill in arrival
    order regardless of size. Sweeping this separately from the differential test isolates
    a priority bug from an accounting bug."""
    rng = random.Random(7)
    for _ in range(50):
        book = LimitOrderBook("FIFO")
        arrivals = [(f"o{i}", rng.randint(1, 50)) for i in range(rng.randint(2, 12))]
        for order_id, qty in arrivals:
            book.add(order_id=order_id, side=Side.SELL, price_ticks=100, qty=qty, owner="mm")

        total = sum(q for _, q in arrivals)
        result = match(book, AggressiveOrder(order_id="agg", side=Side.BUY, qty=total,
                                             price_ticks=100, tif=TimeInForce.IOC))
        filled_order = [f.order_id for f in result.fills]
        assert filled_order == [order_id for order_id, _ in arrivals]
        assert len(book) == 0

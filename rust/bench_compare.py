#!/usr/bin/env python3
"""Time the Rust FIFO book against the Python reference on the same operations.

Run from the repo after building the release extension::

    python -m maturin build --release --manifest-path rust/cockpit-py/Cargo.toml
    python -m pip install --force-reinstall rust/target/wheels/cockpit_quant-*.whl
    python rust/bench_compare.py

Two workloads:

* match walk — one aggressive order clearing a book of 20k resting orders.
  The timer starts after the book is built. The Rust matcher does that
  walk without returning to Python per resting order.
* per-call mutate — 20k individual add/cancel/reduce calls. Each one crosses
  the Python boundary, so this row measures binding overhead as well as the
  book.
* batch mutate — the same 20k ops in one ``apply_quiet`` or ``apply_many``
  call. Quiet skips per-op result tuples. A third row builds the op list
  inside the timer, which is the cost when the tape is not already packed.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from quant.book.lob import LimitOrderBook, PythonLimitOrderBook  # noqa: E402
from quant.book.matching import AggressiveOrder, TimeInForce, match  # noqa: E402
from quant.events import Side  # noqa: E402

SWEEP_N = 20_000
MUTATE_N = 20_000


def _build(book, n: int) -> None:
    for i in range(n):
        book.add(
            order_id=str(i),
            side=Side.SELL,
            price_ticks=100 + (i % 32),
            qty=8,
            seq=i,
            ts=i,
        )


def _match_only(book_cls, n: int) -> None:
    """Time the walk, not the adds that built the book."""
    book = book_cls("S")
    _build(book, n)
    start = time.perf_counter()
    match(
        book,
        AggressiveOrder(
            order_id="agg",
            side=Side.BUY,
            qty=n * 8,
            price_ticks=None,
            tif=TimeInForce.IOC,
        ),
    )
    return time.perf_counter() - start


def _build_pick(book, n: int) -> None:
    for i in range(n):
        book.add(
            order_id=str(i),
            side=Side.BUY if i % 2 == 0 else Side.SELL,
            price_ticks=(100 - (i % 8)) if i % 2 == 0 else (101 + (i % 8)),
            qty=100,
            seq=i,
            ts=i,
        )


def _pick_via_index(book, picks: int) -> None:
    n = len(book)
    for i in range(picks):
        resting = list(book._index.values())
        resting[i % n]


def _pick_via_resting_at(book, picks: int) -> None:
    n = len(book)
    for i in range(picks):
        book.resting_at(i % n)


def _mutate_once(book_cls, n: int) -> None:
    book = book_cls("S")
    live: list[str] = []
    for i in range(n):
        if i % 5 == 4 and live:
            order_id = live.pop()
            book.cancel(order_id)
            continue
        if i % 5 == 3 and live:
            order_id = live[-1]
            qty = book.get(order_id).qty
            book.reduce(order_id, qty)
            live.pop()
            continue
        order_id = f"o{i}"
        book.add(
            order_id=order_id,
            side=Side.BUY if i % 2 == 0 else Side.SELL,
            price_ticks=100 + (i % 16),
            qty=10 + (i % 7),
            seq=i,
            ts=i,
        )
        live.append(order_id)


def _mutate_ops(n: int) -> list[tuple]:
    """The same add/cancel/reduce pattern as ``_mutate_once``, already packed."""
    ops = []
    live: list[tuple[str, int]] = []
    for i in range(n):
        if i % 5 == 4 and live:
            order_id, _qty = live.pop()
            ops.append(("c", order_id))
            continue
        if i % 5 == 3 and live:
            order_id, qty = live.pop()
            ops.append(("r", order_id, qty))
            continue
        order_id = f"o{i}"
        qty = 10 + (i % 7)
        ops.append((
            "a",
            order_id,
            "buy" if i % 2 == 0 else "sell",
            100 + (i % 16),
            qty,
            i,
            i,
            None,
        ))
        live.append((order_id, qty))
    return ops


def _batch_quiet(ops) -> None:
    LimitOrderBook("S").apply_quiet(ops)


def _batch_detailed(ops) -> None:
    LimitOrderBook("S").apply_many(ops)


def _pack_and_quiet(n: int) -> None:
    LimitOrderBook("S").apply_quiet(_mutate_ops(n))


def _paper_oms():
    """Two live quotes and a few hundred terminal ids, the market-maker shape."""
    from quant.events import Side
    from quant.oms import NewOrder, OMS

    oms = OMS()
    for i in range(400):
        oms.register(
            NewOrder(client_id=str(i), symbol="S", side=Side.BUY, qty=10, price_ticks=100),
            strategy="mm",
        )
        if i < 398:
            oms.on_cancelled(str(i))
    return oms


def _scan_every_order(oms, repeats: int) -> None:
    for _ in range(repeats):
        [order for order in oms._orders.values() if order.is_live]


def _read_live_index(oms, repeats: int) -> None:
    for _ in range(repeats):
        oms.live_orders()


def _rate_burst():
    from quant.events import Side
    from quant.guards import GuardLimits, Guards
    from quant.oms import NewOrder

    guards = Guards(limits=GuardLimits(max_orders_per_window=500, order_rate_window_ns=10**18))
    intent = NewOrder(client_id="c", symbol="S", side=Side.BUY, qty=1, price_ticks=1)
    for ts in range(500):
        guards.check(intent, ts=ts)
    return guards, intent


def _rate_copy(recent, repeats: int) -> None:
    window = 10**18
    cap = 500
    for ts in range(repeats):
        window_start = ts - window
        recent = [stamp for stamp in recent if stamp >= window_start]
        if len(recent) >= cap:
            continue
        recent.append(500 + ts)


def _rate_deque(guards, repeats: int) -> None:
    """Time the window only. ``Guards.check`` also prices position and loss limits."""
    rate_ok = guards._rate_ok
    for ts in range(repeats):
        rate_ok(1_000 + ts)


def _python_tape(n: int) -> None:
    from quant.feed.synthetic import SyntheticFeed

    list(SyntheticFeed(symbols=("S",), n_events=n, seed=3)._python_events())


def _native_tape(n: int) -> None:
    from quant.feed.synthetic import SyntheticFeed

    list(SyntheticFeed(symbols=("S",), n_events=n, seed=3))


def _repeat_snapshot(book, repeats: int) -> None:
    for _ in range(repeats):
        book.snapshot()


def _rebuild_snapshot(book, repeats: int) -> None:
    for _ in range(repeats):
        book._snap_key = None
        book.snapshot()


def _time(fn, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def main() -> None:
    if getattr(LimitOrderBook, "backend", "python") != "rust":
        print("cockpit_quant is not installed; build the release extension first")
        sys.exit(1)
    import cockpit_quant
    if not hasattr(cockpit_quant, "synthetic_cursor"):
        print("synthetic_cursor is not installed; build the release extension first")
        sys.exit(1)

    def _median_match(book_cls) -> float:
        samples = sorted(_match_only(book_cls, SWEEP_N) for _ in range(5))
        return samples[len(samples) // 2]

    rust_sweep = _median_match(LimitOrderBook)
    py_sweep = _median_match(PythonLimitOrderBook)
    rust_mutate = _time(lambda: _mutate_once(LimitOrderBook, MUTATE_N))
    py_mutate = _time(lambda: _mutate_once(PythonLimitOrderBook, MUTATE_N))
    packed = _mutate_ops(MUTATE_N)
    rust_quiet = _time(lambda: _batch_quiet(packed))
    rust_detailed = _time(lambda: _batch_detailed(packed))
    rust_pack = _time(lambda: _pack_and_quiet(MUTATE_N))

    def row(name: str, rust: float, python: float) -> str:
        ratio = python / rust if rust else float("inf")
        return f"| {name} | {python * 1e3:.1f} ms | {rust * 1e3:.1f} ms | {ratio:.2f}x |"

    print(f"sweep orders={SWEEP_N}  mutate ops={MUTATE_N}  (median of 5)")
    print("| workload | python | rust | python/rust |")
    print("|---|---:|---:|---:|")
    print(row(f"match walk ({SWEEP_N} resting)", rust_sweep, py_sweep))
    print(row(f"per-call mutate ({MUTATE_N})", rust_mutate, py_mutate))
    print(row(f"batch quiet, ops packed ({MUTATE_N})", rust_quiet, py_mutate))
    print(row(f"batch detailed, ops packed ({MUTATE_N})", rust_detailed, py_mutate))
    print(row(f"batch quiet, pack included ({MUTATE_N})", rust_pack, py_mutate))
    pick_n = 64
    picks = 20_000
    rust_book = LimitOrderBook("S")
    _build_pick(rust_book, pick_n)
    _pick_via_index(rust_book, 4)
    _pick_via_resting_at(rust_book, 4)
    via_index = _time(lambda: _pick_via_index(rust_book, picks))
    via_at = _time(lambda: _pick_via_resting_at(rust_book, picks))
    print(row(f"pick one of {pick_n}, {picks} times", via_at, via_index))
    if via_index / via_at < 1.0:
        sys.exit("resting_at regressed below copying the order index")
    oms = _paper_oms()
    reads = 5_000
    _scan_every_order(oms, 4)
    _read_live_index(oms, 4)
    scanned = _time(lambda: _scan_every_order(oms, reads))
    indexed = _time(lambda: _read_live_index(oms, reads))
    print(row(f"live orders, 2 of 400, {reads} reads", indexed, scanned))
    if scanned / indexed < 1.0:
        sys.exit("live-order index regressed below scanning every order")
    snap_book = LimitOrderBook("S")
    _build_pick(snap_book, 32)
    snap_book.snapshot()
    snap_n = 20_000
    cached = _time(lambda: _repeat_snapshot(snap_book, snap_n))
    rebuilt = _time(lambda: _rebuild_snapshot(snap_book, snap_n))
    print(row(f"snapshot, {snap_n} unchanged reads", cached, rebuilt))
    if rebuilt / cached < 5.0:
        sys.exit("snapshot cache regressed below 5x the cost of rebuilding the depth")
    guards, _intent = _rate_burst()
    rate_n = 5_000
    copied = [500 - 1]
    _rate_copy(copied, 4)
    _rate_deque(guards, 4)
    copy_time = _time(lambda: _rate_copy(list(range(500)), rate_n))
    deque_time = _time(lambda: _rate_deque(guards, rate_n))
    print(row(f"order-rate window, {rate_n} checks", deque_time, copy_time))
    if copy_time / deque_time < 1.0:
        sys.exit("order-rate window regressed below copying the stamp list")
    tape_n = 4_000
    _python_tape(200)
    _native_tape(200)
    python_tape = _time(lambda: _python_tape(tape_n))
    native_tape = _time(lambda: _native_tape(tape_n))
    print(row(f"synthetic tape, {tape_n} events", native_tape, python_tape))
    # Linux runs were 2.88x, 2.72x, and 2.40x. macOS rust-book measured 1.98x
    # (31.8 ms versus 16.1 ms), which misses a 2x floor. Linux's own spread
    # is about 17%, and 1.98 x 0.83 is about 1.64, so the floor is 1.5x.
    # That stays under a noisy macOS sample and still rejects a ratio near 1x.
    if python_tape / native_tape < 1.5:
        sys.exit("synthetic tape regressed below 1.5x the Python generator")


if __name__ == "__main__":
    main()

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

    def _median_match(book_cls) -> float:
        samples = sorted(_match_only(book_cls, SWEEP_N) for _ in range(5))
        return samples[len(samples) // 2]

    rust_sweep = _median_match(LimitOrderBook)
    py_sweep = _median_match(PythonLimitOrderBook)
    rust_mutate = _time(lambda: _mutate_once(LimitOrderBook, MUTATE_N))
    py_mutate = _time(lambda: _mutate_once(PythonLimitOrderBook, MUTATE_N))

    def row(name: str, rust: float, python: float) -> str:
        ratio = python / rust if rust else float("inf")
        return f"| {name} | {python * 1e3:.1f} ms | {rust * 1e3:.1f} ms | {ratio:.2f}x |"

    print(f"sweep orders={SWEEP_N}  mutate ops={MUTATE_N}  (median of 5)")
    print("| workload | python | rust | python/rust |")
    print("|---|---:|---:|---:|")
    print(row(f"match walk ({SWEEP_N} resting)", rust_sweep, py_sweep))
    print(row(f"per-call mutate ({MUTATE_N})", rust_mutate, py_mutate))


if __name__ == "__main__":
    main()

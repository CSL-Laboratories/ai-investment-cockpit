#!/usr/bin/env python3
"""Time Rust online estimators against the Python reference.

Per-call rows cross the interpreter once per observation, which is the binding
the paper strategies use. Batch rows hand the stream over in one call.

    python -m maturin build --release --manifest-path rust/cockpit-py/Cargo.toml
    python -m pip install --force-reinstall rust/target/wheels/cockpit_quant-*.whl
    python rust/bench_signals.py
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import cockpit_quant  # noqa: E402
from quant.signals import (  # noqa: E402
    SIGNALS_BACKEND,
    EwmaVolatility,
    OrderFlowImbalance,
    RollingBeta,
    RollingZScore,
)

N = 20_000


def _prices(n: int) -> list[float]:
    prices = [100.0]
    for i in range(n - 1):
        prices.append(prices[-1] * (1.0 + (0.002 if i % 2 else -0.0015)))
    prices[n // 3] = 0.0
    return prices


def _python_ewma(prices) -> None:
    vol = EwmaVolatility(lam=0.94)
    vol._core = None
    for price in prices:
        vol.update(price)


def _shipped_ewma(prices) -> None:
    vol = EwmaVolatility(lam=0.94)
    for price in prices:
        vol.update(price)


def _batched_ewma(prices) -> None:
    vol = EwmaVolatility(lam=0.94)
    vol.update_many(prices)


def _python_z(values) -> None:
    z = RollingZScore(window=64)
    z._core = None
    for value in values:
        z.update(value)


def _rust_z(values) -> None:
    z = RollingZScore(window=64)
    for value in values:
        z.update(value)


def _python_ofi(qty) -> None:
    ofi = OrderFlowImbalance(window=64)
    ofi._core = None
    for signed in qty:
        ofi.update(signed_qty=signed)


def _rust_ofi(qty) -> None:
    ofi = OrderFlowImbalance(window=64)
    for signed in qty:
        ofi.update(signed_qty=signed)


def _python_beta(xs, ys) -> None:
    beta = RollingBeta(window=64)
    beta._core = None
    for x, y in zip(xs, ys):
        beta.update(x, y)


def _rust_beta(xs, ys) -> None:
    beta = RollingBeta(window=64)
    for x, y in zip(xs, ys):
        beta.update(x, y)


def _median(fn, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def main() -> None:
    if SIGNALS_BACKEND != "rust":
        print("cockpit_quant signals are not installed; build the release extension first")
        sys.exit(1)
    prices = _prices(N)
    values = [math.sin(i / 17.0) for i in range(N)]
    qty = [100 if i % 3 else -40 for i in range(N)]
    xs = [float(i % 80) for i in range(N)]
    ys = [3.0 + 2.5 * x for x in xs]
    _shipped_ewma(prices[:8])
    _batched_ewma(prices[:8])

    py_ewma = _median(lambda: _python_ewma(prices))
    shipped_ewma = _median(lambda: _shipped_ewma(prices))
    batch_ewma = _median(lambda: _batched_ewma(prices))
    py_z = _median(lambda: _python_z(values))
    rust_z = _median(lambda: _rust_z(values))
    batch_z = _median(lambda: cockpit_quant.zscore_replay(64, values))
    py_ofi = _median(lambda: _python_ofi(qty))
    rust_ofi = _median(lambda: _rust_ofi(qty))
    batch_ofi = _median(lambda: cockpit_quant.ofi_replay(64, qty))
    py_beta = _median(lambda: _python_beta(xs, ys))
    rust_beta = _median(lambda: _rust_beta(xs, ys))
    batch_beta = _median(lambda: cockpit_quant.beta_replay(64, xs, ys))

    def row(name: str, python: float, rust: float) -> str:
        ratio = python / rust if rust else float("inf")
        return f"| {name} | {python * 1e3:.1f} ms | {rust * 1e3:.1f} ms | {ratio:.2f}x |"

    print(f"n={N}  (median of 5, release)")
    print("| workload | python | rust | python/rust |")
    print("|---|---:|---:|---:|")
    print(row("per-call EWMA", py_ewma, shipped_ewma))
    print(row("batched EWMA", py_ewma, batch_ewma))
    if py_ewma / shipped_ewma < 1.0 or py_ewma / batch_ewma < 1.0:
        sys.exit("EWMA shipped path regressed below 1x")
    print(row("per-call z-score", py_z, rust_z))
    print(row("batched z-score", py_z, batch_z))
    print(row("per-call OFI", py_ofi, rust_ofi))
    print(row("batched OFI", py_ofi, batch_ofi))
    print(row("per-call beta", py_beta, rust_beta))
    print(row("batched beta", py_beta, batch_beta))


if __name__ == "__main__":
    main()

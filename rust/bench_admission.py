#!/usr/bin/env python3
"""Time Rust dedup and the token bucket against the Python reference.

Per-call rows cross the interpreter once per key or timestamp, which is the
binding a poller uses. Batch rows hand the script over in one call.

    python -m maturin build --release --manifest-path rust/cockpit-py/Cargo.toml
    python -m pip install --force-reinstall rust/target/wheels/cockpit_quant-*.whl
    python rust/bench_admission.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

import cockpit_quant  # noqa: E402
from ingest.dedup import DEDUP_BACKEND, Dedup  # noqa: E402
from ingest.ratelimit import RATELIMIT_BACKEND, TokenBucket, python_allow  # noqa: E402

N = 20_000


def _keys(n: int) -> list[str]:
    return [f"src:{i % (n // 2)}" for i in range(n)]


def _python_dedup(keys) -> None:
    seen = Dedup(capacity=8_192)
    seen._core = None
    for key in keys:
        seen.add(key)


def _rust_dedup(keys) -> None:
    seen = Dedup(capacity=8_192)
    for key in keys:
        seen.add(key)


def _python_bucket(times) -> None:
    bucket = TokenBucket(rate_per_sec=1.0, capacity=5.0, now=0.0)
    for now in times:
        python_allow(bucket, now)


def _shipped_bucket(times) -> None:
    bucket = TokenBucket(rate_per_sec=1.0, capacity=5.0, now=0.0)
    for now in times:
        bucket.allow(now)


def _batched_bucket(times) -> None:
    bucket = TokenBucket(rate_per_sec=1.0, capacity=5.0, now=0.0)
    bucket.allow_many(times)


def _median(fn, repeats: int = 5) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    samples.sort()
    return samples[len(samples) // 2]


def main() -> None:
    if DEDUP_BACKEND != "rust" or RATELIMIT_BACKEND != "rust":
        print("cockpit_quant admission is not installed; build the release extension first")
        sys.exit(1)
    keys = _keys(N)
    times = [i * 0.01 for i in range(N)]
    ops = [("a", key) for key in keys]
    _python_dedup(keys[:8])
    _rust_dedup(keys[:8])
    cockpit_quant.dedup_replay(8192, ops[:8])
    _shipped_bucket(times[:8])
    _batched_bucket(times[:8])

    py_dedup = _median(lambda: _python_dedup(keys))
    rust_dedup = _median(lambda: _rust_dedup(keys))
    batch_dedup = _median(lambda: cockpit_quant.dedup_replay(8192, ops))
    py_bucket = _median(lambda: _python_bucket(times))
    shipped_bucket = _median(lambda: _shipped_bucket(times))
    batch_bucket = _median(lambda: _batched_bucket(times))

    def row(name: str, python: float, rust: float) -> str:
        ratio = python / rust if rust else float("inf")
        return f"| {name} | {python * 1e3:.1f} ms | {rust * 1e3:.1f} ms | {ratio:.2f}x |"

    print(f"n={N}  (median of 5, release)")
    print("| workload | python | rust | python/rust |")
    print("|---|---:|---:|---:|")
    print(row("per-call dedup", py_dedup, rust_dedup))
    print(row("batched dedup", py_dedup, batch_dedup))
    print(row("per-call token bucket", py_bucket, shipped_bucket))
    print(row("batched token bucket", py_bucket, batch_bucket))
    if py_bucket / shipped_bucket < 1.0 or py_bucket / batch_bucket < 1.0:
        sys.exit("token bucket shipped path regressed below 1x")


if __name__ == "__main__":
    main()
